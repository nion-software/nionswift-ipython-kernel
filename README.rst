nion-swift-ipython-kernel
=========================

Introduction
------------

A plugin for Nion Swift that implements an ipython kernel.

It implements the most important messages of the jupyter/ipython `messaging protocol <https://jupyter-client.readthedocs.io/en/latest/messaging.html>`_.

Messages of the clients are received on the `shell channel <https://jupyter-client.readthedocs.io/en/latest/messaging.html#messages-on-the-shell-router-dealer-channel>`_.
These messages are processed by ``MessageHandlers`` and each message type has its own handler that only processes this message
type. New handlers need to inherit from the base ``MessageHandler``  class in ``nion.ipython_kernel.ipython_kernel.py`` and
the handler instance need to be registered with ``IpythonKernel.register_shell_handler``.

The `content <https://jupyter-client.readthedocs.io/en/latest/messaging.html#content>`_ dictionary of a message matching
the handler's type will be passed to a handler's ``process_request`` method. The ``process_request`` method must return
a dictionary containing the `reply content <https://jupyter-client.readthedocs.io/en/latest/messaging.html#request-reply>`_.

To connect a jupyter console to this kernel, run:

``jupyter console --existing nionswift-ipython-kernel.json``

In order to connect a jupyter notebook you need to install ``nionswift-ipython-provisioner`` in the client environment.
This is needed because jupyter notebooks cannot connect to a running kernel by default, so a custom kernel provisioner
is required for this.


Matplotlib integration
----------------------

If matplotlib is installed in the python environment running Swift, this kernel supports inline plotting and in addition
it can send plots to Swift as a data item.

Default is inline plotting. To switch between plot styles, you can use the ``%matplotlib`` line magic:

``%matploltib inline`` enables inline plotting.

``%matplotlib swift`` enables plotting to Swift data items.

Other matplotlib gui backends can also be used, but be aware that they might cause awkward interactions betweem their
event loop and the swift event loop. So it is recommended to only use the two explicitly supported backends listed above.
Calling ``%matplotlib auto`` will also enable the "inline" backend.


Threading
---------

The downside of directly running an ipython kernel within Swift is that long calculations will freeze the Swift UI
for their entire duration. In terms of threading, this ipython kernel behaves exactly like the built-in console
in Swift. So just like it is described in the
`documentation for the built-in console <https://nionswift.readthedocs.io/en/stable/api/concepts.html#console>`_,
you can run your code on a background thread to avoid locking up the UI.

Here is a short example for how to run code on a thread:

.. code-block:: python
  import threading
  import time

  def do_something_slow():
      time.sleep(10.0)
      print('Done doing something slow.')

  threading.Thread(target=do_something_slow).start()

This will print ``"Done doing something slow."`` after 10 seconds, but will not freeze the Swift UI.

Two important notes:

1. If you run code on a thread, you can only access objects that are thread-safe, otherwise you may get undefined
   behavior. As a rule of thumb, any UI access is typically NOT threadsafe. So for example diplaying the result of a
   processing routine as a new data item cannot be done from a thread.

2. The ``threading.Thread(...).start()`` call returns immediately, since the work is done in the background. That means,
   if you need to wait for the result for example to diplay it in Swift, you need to wait until the background thread
   is done before you have access to the result.

The two notes above are the main reason that threading can be a bit tricky to use. If you need to queue a task to be
executed after a processing routine running on a thread has finished, you can use ``api.queue_task()``. This will queue
the function passed to it for execution on the main thread. So for example showing the result data in a new data item
can be accomplished like this:

.. code-block:: python
  import threading
  import time
  import numpy

  def do_something_slow():
      time.sleep(10.0)
      print('Done doing something slow.')
      data = numpy.random.rand(16, 32)
      def show_result():
          data_item = api.library.create_data_item_from_data(data, title='Result')
          api.application.document_windows[0].display_data_item(data_item)
      api.qeue_task(show_result)

  threading.Thread(target=do_something_slow).start()

Note that you must not add the call operator (parenthesis) to the function you are passing to ``queue_task``!

Some more examples for how to interact with the Swift API can be found `here <https://nionswift.readthedocs.io/en/stable/api/scripting.html#scripting-guide>`_.
