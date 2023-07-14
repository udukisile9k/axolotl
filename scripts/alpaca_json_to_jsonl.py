"""Module to convert json file to jsonl"""

import os
import sys
from pathlib import Path
from typing import Optional, Union

import fire

from axolotl.convert import (
    FileReader,
    FileWriter,
    JsonlSerializer,
    JsonParser,
    JsonToJsonlConverter,
    StdoutWriter,
)
from axolotl.logging_config import configure_logging

configure_logging()

# add src to the pythonpath so we don't need to pip install this
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_dir = os.path.join(project_root, "src")
sys.path.insert(0, src_dir)


def main(
    file: Path,
    output: Optional[Path] = None,
    to_stdout: Optional[bool] = False,
):
    """
    Convert a json file to jsonl
    """

    file_reader = FileReader()
    writer: Union[StdoutWriter, FileWriter]
    if to_stdout or output is None:
        writer = StdoutWriter()
    else:
        writer = FileWriter(output)
    json_parser = JsonParser()
    jsonl_serializer = JsonlSerializer()

    converter = JsonToJsonlConverter(file_reader, writer, json_parser, jsonl_serializer)

    converter.convert(file, output)


if __name__ == "__main__":
    fire.Fire(main)
