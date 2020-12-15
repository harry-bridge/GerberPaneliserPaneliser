#! /usr/bin/env python3
"""
Parses a gerber file containing a single letter and add the coords to the font definition file
"""

import logzero
import logging
from pathlib import Path
import re
import json


class GerbLoader:
    logger = None

    gerb_file_path = None

    font_file_path = Path.cwd() / "vector_font.json"
    font_def = None

    letter_name = None
    # List of (x, y, code) tuples gathered from the gerber file
    draw_coords = list()

    def __init__(self, default_file_path=None):
        self.logger = logzero.logger
        logzero.loglevel(logging.DEBUG)

        if default_file_path:
            self.gerb_file_path = Path(default_file_path)

    def load_file(self):
        if self.gerb_file_path is None:
            self.logger.info("Please input path to gerber file")
            self.gerb_file_path = Path(input("File: ").strip().replace("\\ ", " "))

        self.logger.info("Please input what character this gerber file is drawing")
        self.letter_name = input("Letter Name: ").strip()

        if self.font_file_path.exists():
            self.logger.debug("Loading json font definition")
            self.font_def = json.loads(self.font_file_path.read_text())
        else:
            self.logger.error("Font definition file does not exist at: {}".format(str(self.font_file_path)))
            exit(1)

    def parse_file(self):
        """
        Parse the gerber file into a list of coords for drawing the letter
        :return:
        """
        with open(self.gerb_file_path, 'r') as infile:
            for line in infile.readlines():
                # Remove \n from end of lines
                _line = line.strip()

                if _line.startswith("X"):
                    self.logger.debug("Parsing line: {}".format(_line))
                    # x and y are in the form xxx.xxxx
                    # leading 0s are omitted
                    _x = int(re.findall(r"X-?\d+", _line)[0][1:]) / 10000
                    _y = int(re.findall(r"Y-?\d+", _line)[0][1:]) / 10000
                    _code = re.findall(r"D\d+", _line)[0]
                    self.draw_coords.append((_x, _y, _code))

        self.logger.debug("Draw coords: {}".format(self.draw_coords))

    def generate_json(self):
        _char_dict = dict()
        _char_dict[self.letter_name] = list()
        _char = _char_dict[self.letter_name]

        for coord in self.draw_coords:
            _char.append({"x": coord[0], "y": coord[1], "command": coord[2]})

        _write_to_file = 1

        try:
            _ = self.font_def["letters"][self.letter_name]
            self.logger.warning("Character '{}' already exists in font definition file".format(self.letter_name))
            _write_to_file = 0

            self.logger.info("Do you want to overwrite it?")
            _overwrite = input("Overwrite: (Y/N): ")

            if _overwrite.upper() == "Y":
                _write_to_file = 1

        except KeyError:
            pass

        if _write_to_file == 1:
            self.font_def["letters"][self.letter_name] = _char

            self.logger.info("Writing letter coords to font definition")

            with open(self.font_file_path, 'w') as out_file:
                out_file.writelines(json.dumps(self.font_def, indent=2))

        self.logger.info("Letters in file: {}".format(list(self.font_def["letters"].keys())))

    def on_execute(self):
        self.load_file()
        self.parse_file()
        self.generate_json()


if __name__ == '__main__':
    # _path = Path("")
    # app = GerbLoader(_path)
    app = GerbLoader()
    app.on_execute()
