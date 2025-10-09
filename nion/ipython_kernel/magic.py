import typing
import dataclasses
import getopt

from nion.utils import Registry


LINE_MAGIC_REGISTRY_KEY = 'nion-ipython-line-magic'
LINE_MAGIC_IDENTIFIER_CHARACTER = '%'


class InvalidMagicArgs(ValueError):
    ...


class MagicNotFoundError(ValueError):
    ...


@dataclasses.dataclass
class MagicCommandArgs:
    args: tuple[typing.Any, ...]
    kwargs: dict[str, typing.Any]


class Magic(typing.Protocol):
    name: str

    def parse_args(self, magic_string: str) -> MagicCommandArgs:
        ...

    def execute(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        ...


class MatplotlibLineMagic(Magic):
    name = 'matplotlib'

    def parse_args(self, magic_args: str) -> MagicCommandArgs:
        split_string = magic_args.split()
        optlist = list[tuple[str, str]]()
        args = list[str]()
        if split_string:
            optlist, args = getopt.getopt(split_string, 'l', ['list'])
        if not optlist and not args:
            raise InvalidMagicArgs(f'Matplotlib line magic was called without or with wrong arguments: {magic_args}. Usage: %matplotlib [-l | --list] [gui]')

        cleaned_optlist = [(key.lstrip('-'), value) for key, value in optlist]
        return MagicCommandArgs(tuple(args), dict(cleaned_optlist))

    def execute(self, *args: typing.Any, **kwargs: typing.Any) -> None:
        if args:
            import matplotlib
            selected_gui = typing.cast(str, args[0])
            if selected_gui in {'inline', 'auto'}:
                matplotlib.use('module://nion.ipython_kernel.mpl_backend_inline')
            elif selected_gui == 'swift':
                matplotlib.use('module://nion.ipython_kernel.mpl_backend_swift')
            else:
                matplotlib.use(selected_gui)
        elif 'l' in kwargs or 'list' in kwargs:
            print('Existing backends: "inline", "swift"')


def register_line_magic(magic: Magic) -> None:
    Registry.register_component(magic, {LINE_MAGIC_REGISTRY_KEY})


def get_registered_line_magics() -> set[Magic]:
    return typing.cast(set[Magic], Registry.get_components_by_type(LINE_MAGIC_REGISTRY_KEY))


def run_line_magic(magic_string: str) -> None:
    magic_string = magic_string.strip()
    if magic_string.startswith(LINE_MAGIC_IDENTIFIER_CHARACTER):
        magic_string = magic_string[1:]

    split_string = magic_string.split(maxsplit=1)
    if not split_string:
        return
    magic_name = split_string[0]
    for line_magic in get_registered_line_magics():
        if line_magic.name == magic_name:
            break
    else:
        raise MagicNotFoundError(f'A magic command with name "{magic_name}" was not found. '
                                 f'Currently exisiting magic commands are: {[magic.name for magic in get_registered_line_magics()]}')

    command_args = line_magic.parse_args(magic_string[len(line_magic.name):])
    line_magic.execute(*command_args.args, **command_args.kwargs)
