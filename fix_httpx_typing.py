import pathlib

import httpx

pathlib.Path(httpx.__file__).parent.joinpath("py.typed").touch()
