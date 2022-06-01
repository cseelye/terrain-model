#!/usr/bin/env python3
"""Run a script in Blender to finish the model"""

from pathlib import Path
import platform
import sys

from pyapputil.appframework import PythonApp
from pyapputil.argutil import ArgumentParser
from pyapputil.typeutil import ValidateAndDefault, StrType, OptionalValueType
from pyapputil.logutil import GetLogger, logargs
from pyapputil.exceptutil import InvalidArgumentError
from pyapputil.shellutil import Shell

@logargs
@ValidateAndDefault({
    # "arg_name" : (arg_type, arg_default)
    "blender_file": (StrType(), None),
    "map_image": (StrType(), None),
    "background_image": (StrType(), None),
    "preview_file": (OptionalValueType(StrType()), None),
    "collada_file": (OptionalValueType(StrType()), None),
})
def finish_model(blender_file,
                 map_image,
                 background_image,
                 preview_file,
                 collada_file,
):
    log = GetLogger()

    blend_file_path = Path(blender_file).resolve()
    if not blend_file_path.exists():
        raise InvalidArgumentError("Blend file does not exist")

    if not preview_file:
        preview_file = str(blend_file_path.with_name("preview.png"))

    if not collada_file:
        collada_file = str(blend_file_path.with_suffix(".dae"))

    # Get the current directory to add to the PYTHONPATH for blender
    cwd = Path(sys.path[0]).resolve()

    # Find the blender executable
    if platform.system() == "Darwin":
        blender_path = "/Applications/Blender.app/Contents/MacOS/Blender"
    else:
        log.error("This is currently implemented for macOS only.")
        return False

    parts = [
        f"PYTHONPATH={cwd}",
        blender_path,
        blender_file,
        "--python-use-system-env",
        "--python",
        "blender_finish_model.py",
    ]
    if any([map_image, background_image, preview_file, collada_file]):
        parts += ["--"]
    if map_image:
        parts += ["--map-image", map_image]
    if background_image:
        parts += ["--background-image", background_image]
    if preview_file:
        parts += ["--preview-file", preview_file]
    if collada_file:
        parts += ["--collada-file", collada_file]

    cmd = " ".join(parts)
    log.info("Invoking blender...")
    retcode, stdout, stderr = Shell(cmd)
    if stdout:
        log.raw(stdout)

    if retcode == 0:
        log.passed("Successfully modified model")
        return True
    else:
        log.error("Failed to modify model")
        log.warning(stderr)
        return False


if __name__ == '__main__':
    parser = ArgumentParser(description="Modify a blender model")
    parser.add_argument("-b", "--blender-file", type=StrType(), metavar="FILENAME", help="Blender model file to modify")
    parser.add_argument("-m", "--map-image", type=StrType(), metavar="FILENAME", help="image to map over the top of the model")
    parser.add_argument("-i", "--background-image", type=StrType(), metavar="FILENAME", help="image to map over the sides and bottom of the model")
    parser.add_argument("-p", "--preview-file", required=False, type=StrType(), metavar="FILENAME", help="path to save preview image")
    parser.add_argument("-c", "--collada-file", required=False, type=StrType(), metavar="FILENAME", help="path to save collada export")

    args = parser.parse_args_to_dict()

    app = PythonApp(finish_model, args)
    app.Run(**args)
