from __future__ import annotations

import typing
import asyncio
import zmq.asyncio
import logging
import traceback


logger = logging.getLogger('nionswift-ipython-kernel')


class ZMQStream:

    def __init__(self, context: zmq.asyncio.Context, ip: str, port: int, transport: str, socket_type: int, event_loop: asyncio.AbstractEventLoop | None = None, name: typing.Optional[str] = None) -> None:
        self.__name = name
        self.__socket = context.socket(socket_type)
        self.__ip = ip
        self.__port = port
        self.__transport = transport
        self.__event_loop = event_loop or asyncio.get_event_loop()

        self.__recv_callback: typing.Optional[typing.Callable[[list[bytes]], typing.Awaitable[typing.Any]]] = None

        self.__task: typing.Optional[asyncio.Task[None]] = None

        self._init_io_state()

    @property
    def socket(self) -> zmq.asyncio.Socket:
        return self.__socket

    @property
    def port(self) -> int:
        return self.__port

    def _bind_socket(self) -> int:
        port = self.__port
        if port <= 0:
            port = self.__socket.bind_to_random_port(f'{self.__transport}://{self.__ip}')
        else:
            self.__socket.bind(f'{self.__transport}://{self.__ip}:{port}')
        logger.debug(f'Stream {self.__name} bound to port {port}.')
        return port

    def on_recv(self, callback: typing.Callable[[list[bytes]], typing.Awaitable[typing.Any]], copy: bool = True) -> None:
        self.__recv_callback = callback
        return None

    async def _run_recv_callback(self, msg: list[bytes]) -> None:
        if self.__recv_callback:
            prefix = msg[:1]
            reply = await self.__recv_callback(msg)
            if reply:
                async def send_coro() -> None:
                    await self.__socket.send_multipart(prefix + reply, copy=True)
                self.__event_loop.create_task(send_coro())

    async def _watch_for_events(self) -> None:
        while True:
            try:
                logger.debug(f'Receiver {self.__name} polling for events.')
                self.__poll_future = typing.cast(asyncio.Future[typing.Any], self.__socket.poll(flags=zmq.POLLIN))  # type: ignore
                await self.__poll_future
                if (exception := self.__poll_future.exception()):
                    logger.error(f'Future exception in {self.__name}: {exception}')
                msg = await self.__socket.recv_multipart(copy=True)
                self.__event_loop.create_task(self._run_recv_callback(msg))
            except asyncio.CancelledError:
                self.__socket.close(linger=1000)
                logger.debug(f'ZMQStream {self.__name} socket closed.')
                break
            except Exception:
                logger.error(traceback.format_exc())

    def _init_io_state(self) -> None:
        self.__port = self._bind_socket()
        self.__task = self.__event_loop.create_task(self._watch_for_events())

    def close(self) -> None:
        if self.__task:
            self.__event_loop.call_soon_threadsafe(self.__task.cancel)
            if not self.__event_loop.is_running() and not self.__event_loop.is_closed():
                self.__event_loop.run_until_complete(self.__task)
                return
        self.__socket.close(linger=1000)

    async def is_active(self) -> None:
        if self.__task:
            await self.__task

    def add_done_callback(self, callback: typing.Callable[[asyncio.Task[typing.Any]], None]) -> None:
        if self.__task:
            self.__task.add_done_callback(callback)
