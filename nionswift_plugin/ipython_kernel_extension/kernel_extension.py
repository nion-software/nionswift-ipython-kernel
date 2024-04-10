import typing

from nion.ipython_kernel import ipython_kernel

class IPythonKernelExtension:

    extension_id = 'nion.experimental.ipython_kernel'

    def __init__(self, api_broker: typing.Any) -> None:
        api = api_broker.get_api(version='~1.0')
        event_loop = api.application._application.event_loop
        kernel_settings = ipython_kernel.KernelSettings()
        self.kernel = ipython_kernel.IpythonKernel(kernel_settings, event_loop=event_loop)
        execute_handler = ipython_kernel.ExecuteRequestMessageHandler(locals())
        self.kernel.register_shell_handler(execute_handler)
        info_handler = ipython_kernel.KernelInfoMessageHandler()
        self.kernel.register_shell_handler(info_handler)
        is_complete_handler = ipython_kernel.IsCompleteHandler()
        self.kernel.register_shell_handler(is_complete_handler)
        self.kernel.start()

    def close(self) -> None:
        self.kernel.close()
