import typing
import threading
import errno
import zmq


class Heartbeat(threading.Thread):

    def __init__(self, context: zmq.Context[typing.Any], ip: str, port: int, transport: str, ready_callback: typing.Optional[typing.Callable[[], None]] = None):
        super().__init__(daemon=True, name='Heartbeat')
        self.__context = context
        self.__ip = ip
        self.__port = -1
        self.__transport = transport
        self.__original_port = port
        self.__socket = typing.cast(zmq.Socket[typing.Any], None)
        self.__ready_callback = ready_callback

    @property
    def port(self) -> int:
        return self.__port

    def _bind_socket(self, socket: zmq.Socket[typing.Any], port: int) -> int:
        if port <= 0:
            port = socket.bind_to_random_port(f'{self.__transport}://{self.__ip}')
        else:
            socket.bind(f'{self.__transport}://{self.__ip}:{port}')
        return port

    def run(self) -> None:
        self.__socket = self.__context.socket(zmq.ROUTER)
        self.__socket.linger = 1000
        try:
            self.__port = self._bind_socket(self.__socket, self.__original_port)
        except Exception:
            self.__socket.close()
            raise

        if self.__ready_callback:
            self.__ready_callback()

        while True:
            try:
                zmq.device(zmq.QUEUE, self.__socket, self.__socket)  # type: ignore
            except zmq.ZMQError as e:
                if e.errno == errno.EINTR:
                    # signal interrupt, resume heartbeat
                    continue
                if e.errno == zmq.ETERM:
                    # context terminated, close socket and exit
                    try:
                        self.__socket.close()
                    except zmq.ZMQError:
                        # suppress further errors during cleanup
                        # this shouldn't happen, though
                        pass
                    break
                if e.errno == zmq.ENOTSOCK:
                    # socket closed elsewhere, exit
                    break
                raise
            else:
                break
