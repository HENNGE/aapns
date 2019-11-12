import httpx
import pathlib

pathlib.Path(httpx.__file__).parent.joinpath("py.typed").touch()
