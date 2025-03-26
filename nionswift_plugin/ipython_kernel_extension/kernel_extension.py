import typing
import logging
import dataclasses
import uuid

from nion.utils import Registry
from nion.swift.model import DocumentModel
from nion.ipython_kernel import ipython_kernel

logger = ipython_kernel.logger

@dataclasses.dataclass
class ConsoleStartupInfo:
    console_startup_id: str
    console_startup_lines: typing.Sequence[str]
    console_startup_help: typing.Optional[typing.Sequence[str]]


class ConsoleStartupComponent(typing.Protocol):
    def get_console_startup_info(self, logger: logging.Logger) -> ConsoleStartupInfo: ...


class IPythonKernelExtension:

    extension_id = 'nion.experimental.ipython_kernel'

    def __init__(self, api_broker: typing.Any) -> None:
        self.api = api_broker.get_api(version='~1.0')
        self.event_loop = self.api.application._application.event_loop
        kernel_settings = ipython_kernel.KernelSettings()
        self.kernel = ipython_kernel.IpythonKernel(kernel_settings, event_loop=self.event_loop)
        execute_handler = ipython_kernel.ExecuteRequestMessageHandler()
        ipython_kernel.register_shell_handler(execute_handler)
        info_handler = ipython_kernel.KernelInfoMessageHandler()
        ipython_kernel.register_shell_handler(info_handler)
        is_complete_handler = ipython_kernel.IsCompleteHandler()
        ipython_kernel.register_shell_handler(is_complete_handler)
        completion_handler = ipython_kernel.CompleteRequestHandler()
        ipython_kernel.register_shell_handler(completion_handler)
        Registry.register_component(self.kernel, {'nionswift-ipython-kernel'})

        self.event_loop.call_later(1.0, self.init_delayed)
        self.kernel.start()

        self._setup_matplotlib_integration()



    def close(self) -> None:
        self.__item_map_changed_listener = None
        self.__console_startup_registered_listener = None
        Registry.unregister_component(self.kernel, {'nionswift-ipython-kernel'})
        self.kernel.close()

    def init_delayed(self):
        try:
            self.api.library
        except AssertionError:
            self.event_loop.call_later(1.0, self.init_delayed)
        self.__console_startup_registered_listener = typing.cast(typing.Any, Registry.listen_component_registered_event(self._console_startup_component_registered))
        for component in Registry.get_components_by_type("console-startup"):
            self._run_console_startup_lines(typing.cast(ConsoleStartupComponent, component))
        self.__item_map_changed_listener = typing.cast(typing.Any, DocumentModel.MappedItemManager().changed_event.listen(self._update_item_map_items))
        self._update_item_map_items()

    def _run_console_startup_lines(self, component: ConsoleStartupComponent) -> None:
        console_startup_component = typing.cast(ConsoleStartupComponent, component)
        console_startup_info = console_startup_component.get_console_startup_info(logger)
        try:
            if lines := console_startup_info.console_startup_lines:
                compiled_lines = compile('\n'.join(lines), '<string>', 'exec')
                exec(compiled_lines, globals(), self.kernel.kernel_data.namespace)
        except:
            import traceback
            logger.error(f'Error running console startup script with id {console_startup_info.console_startup_id}.\n'
                         f'This is the error traceback:\n{traceback.format_exc()}')

    def _console_startup_component_registered(self, component: typing.Any, component_types: typing.Set[str], **kwargs):
        if "console-startup" in component_types:
            self._run_console_startup_lines(typing.cast(ConsoleStartupComponent, component))

    def _update_item_map_items(self) -> None:
        item_map = DocumentModel.MappedItemManager().item_map
        api_item_map = {name: self.api.library.get_item_by_specifier(self.api.create_specifier(uuid.UUID(str(item.uuid)))) for name, item in item_map.items()}
        self.kernel.kernel_data.namespace.update(api_item_map)

    def _setup_matplotlib_integration(self) -> None:
        try:
            import matplotlib
            matplotlib.use('module://nion.ipython_kernel.mpl_backend_inline')
            logger.info('Using nionswift inline matplotlib backend.')
        except (ImportError, ModuleNotFoundError):
            pass
