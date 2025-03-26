import asyncio
import typing
import io
import base64

from nion.utils import Registry

import matplotlib.pyplot
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backend_bases import FigureManagerBase
from matplotlib._pylab_helpers import Gcf

from nion.ipython_kernel import ipython_kernel as kernel_module


class NionSwiftFigureManager(FigureManagerBase):

    @classmethod
    def pyplot_show(cls, *, block: bool | None = None) -> None:
        ipython_kernel = typing.cast(kernel_module.IpythonKernel, Registry.get_component('nionswift-ipython-kernel'))
        if ipython_kernel:
            asyncio.create_task(ipython_kernel.clear_output(True))
            for figure_manager in Gcf.get_all_fig_managers():
                header = kernel_module.IPythonMessageHeader(msg_id=kernel_module.new_id(),
                                                            session=ipython_kernel._id,
                                                            msg_type='display_data',
                                                            date=kernel_module.current_date())
                figure_file = io.BytesIO()
                figure_manager.canvas.print_figure(figure_file, format='png')
                figure_file.seek(0)
                figure_data = base64.b64encode(figure_file.read())
                message = kernel_module.IPythonMessage(header=header,
                                                       parent_header=ipython_kernel.parent_header,
                                                       metadata=dict(),
                                                       content={'data': {'image/png': figure_data.decode('ASCII')},
                                                                'metadata': dict(),
                                                                'transient': dict()})
                figure_file.close()
                asyncio.create_task(ipython_kernel.send_iopub_message(message, f'kernel.{ipython_kernel._id}.display_data'))
                if Gcf.get_all_fig_managers():
                    matplotlib.pyplot.close('all')



# Matplotlib requires a backend to supply a FigureCanvas class
class FigureCanvas(FigureCanvasAgg):

    manager_class = NionSwiftFigureManager
