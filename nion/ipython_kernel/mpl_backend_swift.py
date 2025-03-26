import numpy

from nion.swift import Facade

import matplotlib.pyplot
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backend_bases import FigureManagerBase
from matplotlib._pylab_helpers import Gcf


class NionSwiftFigureManager(FigureManagerBase):

    @classmethod
    def pyplot_show(cls, *, block: bool | None = None) -> None:
        api = Facade.get_api('1.0')
        for figure_manager in Gcf.get_all_fig_managers():
            if hasattr(figure_manager.canvas, 'buffer_rgba'):
                figure_manager.canvas.draw()
                buffer = figure_manager.canvas.buffer_rgba()
            else:
                continue
            width, height = figure_manager.canvas.get_width_height()
            array = numpy.frombuffer(buffer, dtype=numpy.uint8)
            title = figure_manager.canvas.figure.get_suptitle() or None
            data_item = api.library.create_data_item_from_data(array.reshape((height, width, 4)), title=title)
            api.application.document_controllers[0].display_data_item(data_item)
        if Gcf.get_all_fig_managers():
            matplotlib.pyplot.close('all')


# Matplotlib requires a backend to supply a FigureCanvas class
class FigureCanvas(FigureCanvasAgg):

    manager_class = NionSwiftFigureManager
