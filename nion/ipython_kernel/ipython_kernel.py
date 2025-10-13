from __future__ import annotations

import typing
import types
import ast
import threading
import asyncio
import zmq
import zmq.asyncio
import dataclasses
import hmac
import uuid
import hashlib
import json
import sys
import os
import logging
import traceback
import time
import io
import functools
import code as code_module
import rlcompleter
import re

from nion.utils import Registry
from nion.ipython_kernel import zmqstream
from nion.ipython_kernel import heartbeat
from nion.ipython_kernel import paths
from nion.ipython_kernel import magic

logging.basicConfig()
logger = logging.getLogger('nionswift-ipython-kernel')
logger.setLevel(logging.INFO)


IPythonMessageMetadata: typing.TypeAlias = dict[str, typing.Any]
IPythonMessageContent: typing.TypeAlias = dict[str, typing.Any]
IPythonMessageBuffers: typing.TypeAlias = list[bytes]


PROTOCOL_VERSION = '5.4'

CONNECTION_FILE_NAME = "nionswift-ipython-kernel.json"
MESSAGE_HANDLER_REGISTRY_NAME = 'ipython-message-handler'


def get_shell_message_handlers_by_messsage_type() -> dict[str, MessageHandler]:
    registered_handlers = Registry.get_components_by_type(MESSAGE_HANDLER_REGISTRY_NAME)
    shell_message_handlers: dict[str, MessageHandler] = dict()
    for handler in registered_handlers:
        if not handler.msg_type in shell_message_handlers or handler.priority > shell_message_handlers[handler.msg_type].priority:
            shell_message_handlers[handler.msg_type] = handler
    return shell_message_handlers


def get_shell_message_handler_for_message_type(msg_type: str) -> MessageHandler | None:
    shell_message_handlers = get_shell_message_handlers_by_messsage_type()
    return shell_message_handlers.get(msg_type)


def register_shell_handler(handler: MessageHandler) -> None:
    shell_message_handlers = get_shell_message_handlers_by_messsage_type()
    if handler.msg_type in shell_message_handlers:
        existing_handler = shell_message_handlers[handler.msg_type]
        if handler.priority <= existing_handler.priority:
            logger.warning(f'NOT USING handler {handler.__class__.__name__} for message type {handler.msg_type} because '
                           f'its priority is not higher than exisiting handler {existing_handler.__class__.__name__} '
                           f'({handler.priority} <= {existing_handler.priority}')
        else:
            logger.info(f'Handler {handler.__class__.__name__} for message type {handler.msg_type} will be used instead of '
                        f'exisiting handler {existing_handler.__class__.__name__} because its priority is higher '
                        f'({handler.priority} > {existing_handler.priority}')
    Registry.register_component(handler, {MESSAGE_HANDLER_REGISTRY_NAME})


def clean_message_dict(msg_dict: dict[str, typing.Any], dataclass: typing.Any) -> dict[str, typing.Any]:
    """
    Cleans the message dictionary by removing keys that are not part of the dataclass.
    This is required because new jupyter messaging protocols can introduce new keys that will cause
    errors if we don't remove them.
    Ignoring unknown keys is fine for a kernel, it only needs to process keys that are part of the messaging protocol
    version the kernel implements.
    """
    cleaned_dict = dict()
    for field in dataclasses.fields(dataclass):
        name = field.name
        default = field.default if field.default is not dataclasses.MISSING else field.default_factory
        cleaned_dict[name] = msg_dict.get(name, default)
    return cleaned_dict


@dataclasses.dataclass(kw_only=True)
class IPythonMessageHeader:
    msg_id: str = ''
    session: str = ''
    msg_type: str = ''
    username: str = ''
    date: str = ''
    version: str = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, msg_dict: dict[str, typing.Any]) -> IPythonMessageHeader:
        cleaned_msg_dict = clean_message_dict(msg_dict, cls)
        return cls(**cleaned_msg_dict)

    def as_dict(self) -> typing.Dict[str, typing.Any]:
        if not self.msg_type:
            return dict()
        return dataclasses.asdict(self)


@dataclasses.dataclass(kw_only=True)
class IPythonMessage:
    header: IPythonMessageHeader
    parent_header: IPythonMessageHeader
    metadata: IPythonMessageMetadata
    content: IPythonMessageContent
    buffers: IPythonMessageBuffers = dataclasses.field(default_factory=list)

    @classmethod
    def from_serialized_ipython_message(cls, msg: SerializedIPythonMessage) -> IPythonMessage:
        return cls(header=IPythonMessageHeader.from_dict(json.loads(msg.header)),
                   parent_header=IPythonMessageHeader.from_dict(json.loads(msg.parent_header)),
                   metadata=json.loads(msg.metadata),
                   content=json.loads(msg.content),
                   buffers=msg.buffers.copy())  # type: ignore


@dataclasses.dataclass(kw_only=True)
class SerializedIPythonMessage:
    socket_ids: list[bytes] = dataclasses.field(default_factory=list)
    delimiter: bytes = b'<IDS|MSG>'
    signature: bytes = b''
    header: bytes = b''
    parent_header: bytes = b''
    metadata: bytes = b''
    content: bytes = b''
    buffers: list[bytes | memoryview] = dataclasses.field(default_factory=list)

    @staticmethod
    def encode(string: str, encoding: str ='UTF-8') -> bytes:
        return string.encode(encoding)

    @classmethod
    def from_ipython_message(cls, ipython_message: IPythonMessage) -> SerializedIPythonMessage:
        header = cls.encode(json.dumps(ipython_message.header.as_dict()))
        parent_header = cls.encode(json.dumps(ipython_message.parent_header.as_dict()))
        metadata = cls.encode(json.dumps(ipython_message.metadata))
        content = cls.encode(json.dumps(ipython_message.content))
        # Check that all buffers support buffer protocol and are contiguous (required by zmq)
        for buffer in ipython_message.buffers:
            if isinstance(buffer, memoryview):
                view = buffer
            else:
                try:
                    view = memoryview(buffer)
                except TypeError as e:
                    raise TypeError('Buffer objects must support the buffer protocol.') from e
            if not view.contiguous:
                raise ValueError('Buffers must be contiguous.')

        return cls(header=header, parent_header=parent_header, metadata=metadata, content=content, buffers=ipython_message.buffers.copy())  # type: ignore

    def is_complete(self) -> bool:
        """Check that all required message parts have been filled"""
        return all([bool(obj) for obj in [self.socket_ids, self.header, self.parent_header, self.content]])

    def to_zmq_multipart_message(self) -> list[bytes | memoryview]:
        message = []
        message.extend(self.socket_ids)
        message.append(self.delimiter)
        message.append(self.signature)
        message.append(self.header)
        message.append(self.parent_header)
        message.append(self.metadata)
        message.append(self.content)
        message.extend(self.buffers)  # type: ignore
        return message  # type: ignore

    @staticmethod
    def _part_bytes(message_part: bytes | zmq.Frame) -> bytes:
        return message_part if isinstance(message_part, bytes) else message_part.bytes

    @classmethod
    def from_zmq_multipart_message(cls, message: typing.Sequence[bytes | zmq.Frame]) -> SerializedIPythonMessage:
        serialized_ipython_message = cls()
        delimiter_position = -1
        for i, part in enumerate(message):
            part_bytes = cls._part_bytes(part)
            if part_bytes == serialized_ipython_message.delimiter:
                delimiter_position = i
                break
        else:
            raise ValueError('Message object does not contain a delimiter')

        serialized_ipython_message.socket_ids = [cls._part_bytes(part) for part in message[:delimiter_position]]
        serialized_ipython_message.signature = cls._part_bytes(message[delimiter_position + 1])
        serialized_ipython_message.header = cls._part_bytes(message[delimiter_position + 2])
        serialized_ipython_message.parent_header = cls._part_bytes(message[delimiter_position + 3])
        serialized_ipython_message.metadata = cls._part_bytes(message[delimiter_position + 4])
        serialized_ipython_message.content = cls._part_bytes(message[delimiter_position + 5])
        if len(message) > delimiter_position + 6:
            buffer_parts = message[delimiter_position + 6:]
            SerializedIPythonMessage.buffers = [part if isinstance(part, bytes) else part.buffer for part in buffer_parts]
        return serialized_ipython_message


def new_id() -> str:
    return str(uuid.uuid4())

def current_date() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


@dataclasses.dataclass(kw_only=True)
class KernelSettings:
    transport: str = 'tcp'
    ip: str = '127.0.0.1'
    shell_port: int = 0
    iopub_port: int = 0
    control_port: int = 0
    stdin_port: int = 0
    hb_port: int = 0
    signature_scheme: str = 'hmac-sha256'


@dataclasses.dataclass(kw_only=True)
class ConnectionInfo:
    control_port: int
    shell_port: int
    transport: str
    signature_scheme: str
    stdin_port: int
    hb_port: int
    ip: str
    iopub_port: int
    key: str

    def write_to_file(self, path: str) -> None:
        self_dict = dataclasses.asdict(self)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w+') as f:
            json.dump(self_dict, f, indent=2)


@dataclasses.dataclass(kw_only=True)
class KernelData:
    execution_counter: int = 0
    namespace: dict[str, typing.Any] = dataclasses.field(default_factory=dict)


class MessageHandler(typing.Protocol):
    msg_type: str # Message type this handler can process
    reply_msg_type: str # Message type of replies coming from this handler
    priority: int # If a handler for a message type already exists, registering a new handler will overwrite the existing
                  # one only if the new handler has a higher priority than the exisiting one. The default handlers
                  # implemented here all have priority 0, so any custom handler with positive priority will get used.

    def process_request(self, kernel_data: KernelData, content: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:
        raise NotImplementedError()


class EmptyCodeError(ValueError):
    ...


class ExecuteRequestMessageHandler(MessageHandler):

    msg_type = 'execute_request'
    reply_msg_type = 'execute_reply'

    @staticmethod
    def preprocess_code(code: str) -> str:
        # Find line magic statements and remove them from the code string because they will produce syntax errors
        split_code = code.splitlines()
        no_line_magic_code: list[str] = []
        for line in split_code:
            if line.strip().startswith(magic.LINE_MAGIC_IDENTIFIER_CHARACTER):
                magic.run_line_magic(line)
            else:
                no_line_magic_code.append(line)
        return '\n'.join(no_line_magic_code)

    @staticmethod
    def compile_code(code: str) -> typing.List[types.CodeType]:
        if not code:
            raise EmptyCodeError()
        ast_obj = ast.parse(code)
        compiled_code: typing.List[types.CodeType] = list()
        # If we have a single statement, we can just compile it and return
        if len(ast_obj.body) < 2:
            compiled_code.append(compile(code, '<string>', 'single'))
            return compiled_code
        # If we have multiple statements, we try to separate the last one from the rest, so that
        # statements like "x + 1" in the last line of a code block get printed automatically, which
        # imitates what ipython does.
        if isinstance(ast_obj.body[-1], ast.Expr):
            # Try to unparse the last expression and compare it to the input code. If it matches, we
            # try to compile everything but the last expression
            unparsed = ast.unparse(ast_obj.body[-1]) # type: ignore
            if unparsed == code[-len(unparsed):]:
                try:
                    compiled_code.append(compile(code[:-len(unparsed)], '<string>', 'exec'))
                    compiled_code.append(compile(unparsed, '<string>', 'single'))
                except:
                    pass
                else:
                    return compiled_code
        # If we end up here, either the last statement of the code block was not an expression or our
        # separation didn't work. As fallback, compile code object in "exec" mode, which will not auto-
        # print expression return values but will always work if the code block was valid.
        compiled_code.append(compile(code, '<string>', 'exec'))
        return compiled_code

    @staticmethod
    def run_code(code_list: list[types.CodeType], locals_: dict[str, typing.Any] | None = None) -> None:
        for c in code_list:
            locals_ = locals_ if locals_ is not None else locals()
            exec(c, locals_)

    def process_request(self, kernel_data: KernelData, content: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:
        if not content.get('silent') and content.get('store_history'):
            kernel_data.execution_counter += 1
        status = 'ok'
        exception: typing.Optional[BaseException] = None
        try:
            code = typing.cast(str, content['code'])
            pre_processed_code = ExecuteRequestMessageHandler.preprocess_code(code)
            compiled = ExecuteRequestMessageHandler.compile_code(pre_processed_code)
        except EmptyCodeError:
            pass
        except Exception as e:
            status = 'error'
            exception = e
        else:
            try:
                ExecuteRequestMessageHandler.run_code(compiled, locals_=kernel_data.namespace)
            except BaseException as e:
                status = 'error'
                exception = e
        result: typing.Dict[str, typing.Any] = {'status': status, 'execution_count': kernel_data.execution_counter}
        if status == 'ok':
            result['user_expressions'] = dict()
        elif status == 'error':
            if exception is not None:
                result['ename'] = type(exception).__name__
                result['evalue'] = str(exception)
                result['traceback'] = traceback.format_tb(exception.__traceback__)
                traceback.print_exception(exception)
        return result


class KernelInfoMessageHandler(MessageHandler):
    msg_type = 'kernel_info_request'
    reply_msg_type = 'kernel_info_reply'

    def process_request(self, kernel_data: KernelData, content: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:
        return {'status': 'ok',
                'protocol_version': PROTOCOL_VERSION,
                'implementation': 'nionswift',
                'implementation_version': '1.0.0',
                'language_info': {
                    'name': 'python',
                    'version': sys.version.split()[0],
                    'mimetype': 'text/x-python',
                    'file_extension': '.py'},
                'banner': 'Connected to the Nion Swift ipython kernel.\n',
                'debugger': False}


class IsCompleteHandler(MessageHandler):
    msg_type = 'is_complete_request'
    reply_msg_type = 'is_complete_reply'

    def process_request(self, kernel_data: KernelData, content: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:
        code = typing.cast(str, content['code'])
        status = 'unknown'
        try:
            compiled = code_module.compile_command(code.rstrip() + '\n')
        except:
            status = 'invalid'
        else:
            if compiled:
                status = 'complete'
            else:
                status = 'incomplete'

        reply = {'status': status}

        if status == 'incomplete':
            indent = '    '
            split_code = code.splitlines()
            last_line = split_code[-1]
            if len(last_line) != len(last_line.lstrip()):
                indent = (len(last_line) - len(last_line.lstrip())) * last_line[0]
            reply['indent'] = indent

        return reply


class CompleteRequestHandler(MessageHandler):
    msg_type = 'complete_request'
    reply_msg_type = 'complete_reply'

    delims = " \t\n`~!@#$%^&*()-=+[{]}\\|;:\'\",<>/?"

    def process_request(self, kernel_data: KernelData, content: typing.Dict[str, typing.Any]) -> typing.Dict[str, typing.Any]:
        """
        content = {
        # The code context in which completion is requested
        # this may be up to an entire multiline cell, such as
        # 'foo = a.isal'
        'code' : str,

        # The cursor position within 'code' (in unicode characters) where completion is requested
        'cursor_pos' : int,
        }


        content = {
        # status should be 'ok' unless an exception was raised during the request,
        # in which case it should be 'error', along with the usual error message content
        # in other messages.
        'status' : 'ok'

        # The list of all matches to the completion request, such as
        # ['a.isalnum', 'a.isalpha'] for the above example.
        'matches' : list,

        # The range of text that should be replaced by the above matches when a completion is accepted.
        # typically cursor_end is the same as cursor_pos in the request.
        'cursor_start' : int,
        'cursor_end' : int,

        # Information that frontend plugins might use for extra display information about completions.
        'metadata' : dict,
        }
        """
        code = content.get('code', '')
        cursor_pos = typing.cast(int, content.get('cursor_pos'))
        cursor_start = cursor_pos
        completion_code = code[:cursor_pos]
        status = 'ok'
        exception = None
        matches: list[str] = []
        rx = "([" + re.escape(CompleteRequestHandler.delims) + "])"
        # the parenthesis around rx make it a group. This will cause split to keep the characters in rx in the
        # list, so that we can reconstruct the original string later
        split_code = re.split(rx, completion_code)
        if len(split_code) > 0:
            completion_term = split_code[-1]
            if completion_term.strip():
                cursor_start = cursor_pos - len(completion_term)
                try:
                    completer = rlcompleter.Completer(namespace=kernel_data.namespace)
                    for i in range(100):
                        match = completer.complete(completion_term, i)
                        if match is not None:
                            matches.append(match)
                        else:
                            break
                except Exception as e:
                    status = 'error'
                    exception = e

        result: dict[str, typing.Any] = {'status': status, 'metadata': dict()}
        if status == 'ok':
            result['matches'] = matches
            result['cursor_start'] = cursor_start
            result['cursor_end'] = cursor_pos
        elif status == 'error':
            if exception is not None:
                result['ename'] = type(exception).__name__
                result['evalue'] = str(exception)
                result['traceback'] = traceback.format_tb(exception.__traceback__)
                traceback.print_exception(exception)
        return result


class StdStreamCatcher(io.TextIOWrapper):

    def __init__(self, buffer: typing.Any, event_loop: asyncio.AbstractEventLoop, bufsize: int = 100, existing_stream: typing.Any = None, **kwargs: typing.Any) -> None:
        super().__init__(buffer, **kwargs)
        self.__event_loop = event_loop
        self.__bufsize = bufsize
        self.__existing_stream = existing_stream
        self.__lock = threading.RLock()
        self.__buffer: list[str] = list()
        self.__handle: typing.Optional[asyncio.Task[typing.Any]] = None
        self.on_stream_write: typing.Optional[typing.Callable[[str], None]] = None

    async def _periodic(self, *, delay: float = 0.0) -> None:
        await asyncio.sleep(delay)
        with self.__lock:
            if self.__buffer:
                self._flush()
        self.__handle = None

    def write(self, s: str) -> int:
        if self.on_stream_write:
            with self.__lock:
                self.__buffer.append(s)
                if len(self.__buffer) > self.__bufsize:
                    self._flush()
                else:
                    self.__handle = self.__event_loop.create_task(self._periodic(delay=0.06))
        if self.__existing_stream:
            self.__existing_stream.write(s)
        else:
            return super().write(s)
        return len(s)
    
    def _flush(self) -> None:
        with self.__lock:
            if self.__buffer and self.on_stream_write:
                s = ''.join(self.__buffer)
                self.__buffer.clear()
                self.on_stream_write(s)

    def flush(self) -> None:
        self._flush()
        if self.__existing_stream:
            self.__existing_stream.flush()
        else:
            return super().flush()
    
    def close(self) -> None:
        try:
            if self.__handle:
                self.__handle.cancel()
        except AttributeError:
            pass
        self._flush()
        return super().close()


class IpythonKernel:
    def __init__(self,
                 settings: KernelSettings,
                 event_loop: asyncio.AbstractEventLoop | None = None,
                 kernel_data: KernelData | None = None) -> None:
        self.__settings = settings
        self.__event_loop = event_loop or asyncio.get_event_loop()
        self.__kernel_data = kernel_data if kernel_data is not None else KernelData()

        self.__context = zmq.asyncio.Context()
        self.__iopub_socket = typing.cast(zmq.Socket[typing.Any], None)

        self.__shell_stream = typing.cast(zmqstream.ZMQStream, None)
        self.__control_stream = typing.cast(zmqstream.ZMQStream, None)
        self.__stdin_stream = typing.cast(zmqstream.ZMQStream, None)

        self._id = new_id()
        self._key = new_id()
        self._digester = hmac.HMAC(self._key.encode('UTF-8'), digestmod=hashlib.sha256)

        self.__connection_info = ConnectionInfo(control_port=settings.control_port,
                                                shell_port=settings.shell_port,
                                                transport=settings.transport,
                                                signature_scheme=settings.signature_scheme,
                                                stdin_port=settings.stdin_port,
                                                hb_port=settings.hb_port,
                                                ip=settings.ip,
                                                iopub_port=settings.iopub_port,
                                                key=self._key)

        self.__control_thread = typing.cast(threading.Thread, None)
        self.__heartbeat_thread = typing.cast(heartbeat.Heartbeat, None)

        self.__parent_header: typing.Optional[IPythonMessageHeader] = None

        self.__stdout_catcher = typing.cast(StdStreamCatcher, None)
        self.__stderr_catcher = typing.cast(StdStreamCatcher, None)
        self.__exisiting_stdout: typing.Any = None
        self.__exisiting_stderr: typing.Any = None

    @property
    def kernel_data(self) -> KernelData:
        return self.__kernel_data

    @property
    def parent_header(self) -> IPythonMessageHeader:
        return self.__parent_header or IPythonMessageHeader()

    def close(self) -> None:
        self.__shell_stream.close()
        self.__control_stream.close()
        self.__stdin_stream.close()
        self.__stdout_catcher.close()
        self.__stderr_catcher.close()
        self.__stdout_catcher = typing.cast(StdStreamCatcher, None)
        self.__stderr_catcher = typing.cast(StdStreamCatcher, None)
        sys.stdout = self.__exisiting_stdout
        sys.stderr = self.__exisiting_stderr
        self.__exisiting_stdout = None
        self.__exisiting_stderr = None
        self.__iopub_socket.close(linger=1000)
        self.__context.term()
        self._remove_connection_file()

    def start(self) -> str:
        self._create_streams()
        assert sys.__stdout__ is not None
        assert sys.__stderr__ is not None
        self.__stdout_catcher = StdStreamCatcher(sys.__stdout__.buffer, self.__event_loop, existing_stream=sys.stdout if sys.stdout is not sys.__stdout__ else None)
        self.__stdout_catcher.on_stream_write = self._send_stdout_message_to_iopub
        self.__exisiting_stdout = sys.stdout
        sys.stdout = self.__stdout_catcher
        self.__stderr_catcher = StdStreamCatcher(sys.__stderr__.buffer, self.__event_loop, existing_stream=sys.stderr if sys.stderr is not sys.__stderr__ else None)
        self.__stderr_catcher.on_stream_write = self._send_stderr_message_to_iopub
        self.__exisiting_stderr = sys.stderr
        sys.stderr = self.__stderr_catcher
        return self._write_connection_file()

    def _send_sys_stream_message_to_iopub(self, stream_name: str, msg: str) -> None:
        header = IPythonMessageHeader(msg_id=new_id(), session=self._id, msg_type='stream', date=current_date())
        message = IPythonMessage(header=header, parent_header=self.parent_header, metadata=dict(), content={'name': stream_name, 'text': msg})
        self.__event_loop.create_task(self.send_iopub_message(message, f'stream.{stream_name}'))

    _send_stdout_message_to_iopub = functools.partialmethod(_send_sys_stream_message_to_iopub, 'stdout')
    _send_stderr_message_to_iopub = functools.partialmethod(_send_sys_stream_message_to_iopub, 'stderr')

    def _bind_socket(self, socket: zmq.Socket[typing.Any], port: int) -> int:
        if port <= 0:
            port = socket.bind_to_random_port(f'{self.__settings.transport}://{self.__settings.ip}')
        else:
            socket.bind(f'{self.__settings.transport}://{self.__settings.ip}:{port}')
        return port

    def _create_streams(self) -> None:
        self.__shell_stream = zmqstream.ZMQStream(self.__context, self.__settings.ip, self.__settings.shell_port, self.__settings.transport, zmq.ROUTER, self.__event_loop, name='shell stream')
        self.__connection_info.shell_port = self.__shell_stream.port
        self.__shell_stream.on_recv(self.process_shell_message)
        self.__stdin_stream = zmqstream.ZMQStream(self.__context, self.__settings.ip, self.__settings.stdin_port, self.__settings.transport, zmq.ROUTER, self.__event_loop, name='stdin stream')
        self.__connection_info.stdin_port = self.__stdin_stream.port
        self.__iopub_socket = self.__context.socket(zmq.PUB)
        self.__connection_info.iopub_port = self._bind_socket(self.__iopub_socket, self.__settings.iopub_port)

        ready_event = threading.Event()
        def make_control_thread() -> None:
            async def run_control_stream() -> None:
                self.__control_stream = zmqstream.ZMQStream(self.__context, self.__settings.ip, self.__settings.control_port, self.__settings.transport, zmq.ROUTER, event_loop=asyncio.get_running_loop(), name='control stream')
                self.__control_stream.on_recv(self.process_control_message)
                self.__connection_info.control_port = self.__control_stream.port
                ready_event.set()
                await self.__control_stream.is_active()
            # On Windows, the default event loop does not support add_reader, which is required by zmq.
            # Therefore, we need to use the SelectorEventLoop on Windows.
            loop_factory = asyncio.SelectorEventLoop if sys.platform == 'win32' else None
            asyncio.run(run_control_stream(), loop_factory=loop_factory)

        self.__control_thread = threading.Thread(target=make_control_thread, daemon=True)
        self.__control_thread.start()
        assert ready_event.wait(5.0), 'Control thread did not start successfully.'
        ready_event.clear()

        def update() -> None:
            self.__connection_info.hb_port = self.__heartbeat_thread.port
            ready_event.set()
        self.__heartbeat_thread = heartbeat.Heartbeat(self.__context, self.__settings.ip, self.__settings.hb_port, self.__settings.transport, ready_callback=update)
        self.__heartbeat_thread.start()
        assert ready_event.wait(5.0), 'Heartbeat thread did not start successfully.'

    def _write_connection_file(self) -> str:
        connection_file_path = os.path.join(paths.jupyter_runtime_dir(), CONNECTION_FILE_NAME)
        self.__connection_info.write_to_file(connection_file_path)
        return connection_file_path

    def _remove_connection_file(self) -> None:
        connection_file_path = os.path.join(paths.jupyter_runtime_dir(), CONNECTION_FILE_NAME)
        try:
            os.remove(connection_file_path)
        except Exception as e:
            logger.error(f'Could not remove connection file {connection_file_path}. Reason: {str(e)}.')

    def _sign_message(self, msg: SerializedIPythonMessage) -> None:
        d = self._digester.copy()
        for serialized_dict in (msg.header, msg.parent_header, msg.metadata, msg.content):
            d.update(serialized_dict)
        msg.signature = bytes(d.hexdigest(), 'UTF-8')

    async def publish_kernel_state(self, state: str, parent_header: IPythonMessageHeader) -> None:
        header = IPythonMessageHeader(msg_id=new_id(), session=self._id, msg_type='status', date=current_date())
        message = IPythonMessage(header=header, parent_header=parent_header, metadata=dict(), content={'execution_state': state})
        await self.send_iopub_message(message, f'kernel.{self._id}.status')

    def prepare_shell_message(self, msg: IPythonMessage) -> list[bytes]:
        serialized_message = SerializedIPythonMessage.from_ipython_message(msg)
        serialized_message.socket_ids = [bytes(msg.header.session, 'UTF-8')]

        if not serialized_message.is_complete():
            logger.warning('Sending an incomplete message on the shell socket. This might cause errors in connected frontends.')

        self._sign_message(serialized_message)

        return typing.cast(list[bytes], serialized_message.to_zmq_multipart_message())

    async def send_iopub_message(self, msg: IPythonMessage, topic: str) -> None:
        serialized_message = SerializedIPythonMessage.from_ipython_message(msg)
        serialized_message.socket_ids = [bytes(topic, 'UTF-8')]

        if not serialized_message.is_complete():
            logger.warning('Sending incomplete message on IOPub socket. This might cause errors in connected frontends.')

        self._sign_message(serialized_message)
        logger.debug(f'Sending iopub message with topic {topic}:\n{dataclasses.asdict(msg)}')
        await self.__iopub_socket.send_multipart(serialized_message.to_zmq_multipart_message(), copy=True)

    async def clear_output(self, wait: bool) -> None:
        header = IPythonMessageHeader(msg_id=new_id(), session=self._id, msg_type='clear_output', date=current_date())
        message = IPythonMessage(header=header, parent_header=self.parent_header, metadata=dict(), content={'wait': wait})
        await self.send_iopub_message(message, f'kernel.{self._id}.clear_output')

    async def process_shell_message(self, msgs: typing.Sequence[bytes | zmq.Frame]) -> list[bytes]:
        reply_msg_type = 'error'
        result_message: typing.Optional[IPythonMessage] = None
        try:
            serialized_ipython_message = SerializedIPythonMessage.from_zmq_multipart_message(msgs)
            ipython_message = IPythonMessage.from_serialized_ipython_message(serialized_ipython_message)
            try:
                action = ipython_message.header.msg_type.split('_')[:-1]
            except:
                pass
            else:
                reply_msg_type = '_'.join(action + ['reply'])
            logger.debug(f'Got shell message: {ipython_message.header.msg_type}:\n{dataclasses.asdict(ipython_message)}')
            await self.publish_kernel_state('busy', ipython_message.header)

            handler = get_shell_message_handler_for_message_type(ipython_message.header.msg_type)
            if handler:
                logger.debug(f'Calling handler {handler.msg_type}')
                self.__parent_header = ipython_message.header
                reply_msg_type = handler.reply_msg_type
                result = handler.process_request(self.__kernel_data, ipython_message.content)
                result_header = IPythonMessageHeader(msg_id=new_id(),
                                                     session=self._id,
                                                     msg_type=handler.reply_msg_type,
                                                     username=ipython_message.header.username,
                                                     date=current_date())
                result_message = IPythonMessage(header=result_header,
                                                parent_header=ipython_message.header,
                                                metadata=dict(),
                                                content=result)
                logger.debug(f'Result message for {ipython_message.header.msg_type}:\n{dataclasses.asdict(result_message)}')
            else:
                # Send empty reply if we do not handle this message type.
                result_message = None
        except Exception as exception:
            result = {'status': 'error'}
            result['ename'] = type(exception).__name__
            result['evalue'] = str(exception)
            result['traceback'] = str(exception.__traceback__)
            result_header = IPythonMessageHeader(msg_id=new_id(),
                                                 session=self._id,
                                                 msg_type=reply_msg_type)
            result_message = IPythonMessage(header=result_header,
                                            parent_header=self.parent_header,
                                            metadata=dict(),
                                            content=result)
        finally:
            self.__event_loop.create_task(self.publish_kernel_state('idle', self.parent_header))
            return self.prepare_shell_message(result_message) if result_message else list()


    async def process_control_message(self, msgs: list[bytes]) -> list[bytes]:
        return []
