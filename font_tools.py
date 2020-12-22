#! /usr/bin/env python3
"""
Parses the vector font file and does various processing operations
A copy of the font def dict is made when it is read from the file, this is what is written back to the font file
"""

import logzero
import logging
from pathlib import Path
import json


class FontTools:
    logger = None

    font_file_path = Path.cwd() / "vector_font.json"
    font_def = None
    font_def_copy = None

    def __init__(self):
        self.logger = logzero.logger
        logzero.loglevel(logging.DEBUG)

    def load_vector_font(self):
        if self.font_file_path.exists():
            self.logger.debug("Loading json font definition")
            self.font_def = json.loads(self.font_file_path.read_text())

            self.font_def_copy = self.font_def.copy()
        else:
            self.logger.error("Font definition file does not exist at: {}".format(str(self.font_file_path)))
            exit(1)

    def write_vector_font_file(self):
        self.logger.info("Writing vector font file")

        with open(self.font_file_path, 'w') as out_file:
            out_file.writelines(json.dumps(self.font_def_copy, indent=2))

    def add_width_to_letters(self):
        """
        Adds width information to each of the letters in the font def for better calculations
        :return:
        """
        # self.logger.debug(self.font_def["letters"])
        for letter, coords in self.font_def["letters"].items():
            self.logger.debug("Reading letter: {}".format(letter))

            # coords = letter[0]

            xmin = float('inf')
            xmax = float('-inf')

            for item in coords:
                # self.logger.debug(item)
                xmin = min(xmin, item['x'])
                xmax = max(xmax, item['x'])

            letter_width = round(xmax - xmin, 4)
            self.logger.debug("Letter width: {}".format(letter_width))
            self.font_def_copy["letters"][letter] = {
                'width': letter_width,
                'coords': coords
            }

        self.logger.debug("Updated font def: {}".format(self.font_def_copy))

    def on_execute(self):
        self.load_vector_font()
        self.add_width_to_letters()
        self.write_vector_font_file()


if __name__ == '__main__':
    app = FontTools()
    app.on_execute()
