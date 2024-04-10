nion-swift-ipython-kernel
=========================

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
