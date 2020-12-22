#! /usr/bin/env python3

import json
from configparser import ConfigParser
from pathlib import Path
import datetime
import logzero
import logging
from zipfile import ZipFile
import shutil

# Partial header for gerber file generation
gerber_header = """G04 Paneliser Gerber RS-274X export*
G75*
%MOMM*%
%FSLAX34Y34*%
%LPD*%
%IN{}*%
%IPPOS*%
"""

# Partial header for excellon file generation
excellon_header = """M48
;GenerationSoftware,Autodesk,EAGLE,9.6.2*%
;CreationDate,{}*%
FMAT,2
ICI,OFF
METRIC,TZ,000.000
""".format(datetime.datetime.now().strftime("%Y-%M-%dT%H:%M:%SZ"))


class GerberGenerator:
    # {width, height, step, repeat, title}
    panel_info = dict()
    config = None
    out_path = None
    # zip_out_path = None

    # Relative points from the corners of the panel, y coord is always the middle of the frame edge
    # order is bl, br, tl, tr
    fid_points = [15, -10, 10, -10]
    # Actual coords of the placed fids, used for reporting
    fid_coords = list()
    # Diameter of the copper fiducial dot, diameter
    fid_dia = 1
    # Diameter of the fiducial solder mask
    fid_soldermask_dia = 2.5

    # Diameter of drill in the 4 corners of the panel
    drill_dia = 3.0
    drill_coords = list()

    # List of file paths to compress into a single zip archive
    file_list = list()
    font_definition = None
    # How high to make the text
    text_size = 1.2
    # How thick to make the text as a percentage of the height
    # i.e for 1.2mm text, a ratio of 10% will result in 0.12mm thick text
    text_ratio = 10

    logger = None

    def __init__(self, logger=None):
        if logger:
            self.logger = logger
        else:
            self.logger = logzero.logger
            logzero.loglevel(logging.DEBUG)

    def _make_output_dir(self):
        """
        Make the output directory to put the individual gerber files
        :return:
        """
        if not self.out_path.is_dir():
            self.logger.debug("Making temporary output directory")
            self.out_path.mkdir()

    def _clean_output_dir(self):
        """
        Remove temporary output gerber files directory and contents
        :return:
        """
        self.logger.debug("Removing temporary output files")
        shutil.rmtree(self.out_path)

    def _zip_output_dir(self):
        """
        Zip up the output gerber files
        :return:
        """
        self.logger.debug("== Writing Zip File ==")
        _out_zip_path = self.out_path.parent / "panel_frame_overlay.zip"

        with ZipFile(str(_out_zip_path), 'w') as out_zip:
            for _file in self.file_list:
                # Zip path and specify location of file in zip archive
                out_zip.write(str(_file), _file.name)

        return str(_out_zip_path)

    def _load_font(self):
        """
        Load the vector font definition into memory
        :return:
        """
        self.logger.debug("Loading font file")
        _font_path = Path.cwd() / "vector_font.json"
        if not _font_path.exists():
            self.logger.error("Font file cannot be found at: {}".format(str(_font_path)))
            exit(1)

        self.font_definition = json.loads(_font_path.read_text())

    def _text_to_silk_mm(self, text):
        """
        Returns the approximate length of a string of text in mm when applied to the silkscreen layer of the PCB
        :param text: A string of text to get the mm length of
        :return: The length of the string in mm when on the PCB
        """
        _string_len = 0

        for letter in text:
            if letter == " ":
                # Space char width
                _string_len += self.font_definition['space_char_width']
            else:
                _string_len += (self.font_definition['letters'][letter]['width'] * self.text_size) + \
                               (self.font_definition['text_letter_gap'] * self.text_size)

        return _string_len

    def _add_text_to_silk_file(self, text, file, x_start, y_start):
        """
        Adds a sting of text to the given silkscreen file
        :param text: String of text to write to the silkscreen file
        :param file: File to write the silkscreen information to
        :return:
        """
        # Remove leading and trailing whitespace in the text
        _text = text.strip()

        with open(file, 'a') as out_file:
            for index, letter in enumerate(_text):
                if letter == " ":
                    x_start += self.font_definition["space_char_width"] - self.font_definition["text_letter_gap"]
                else:
                    try:
                        _xmax = 0
                        for coords in self.font_definition["letters"][str(letter)]['coords']:
                            # print(coords)
                            _x = ((coords["x"] * self.text_size) + x_start)
                            if _x > _xmax:
                                # Store the maximum X coord when drawing the letter
                                _xmax = _x
                            _y = ((coords["y"] * self.text_size) + y_start)
                            out_file.write("X{}Y{}{}*\n".format(int(_x * 10000), int(_y * 10000), coords["command"]))

                        x_start = _xmax + self.font_definition["text_letter_gap"]
                    except KeyError:
                        self.logger.error("Letter '{}' not found in font definition file".format(letter))
                        self.logger.error("Please try again with a different frame title")
                        exit(1)

    def _write_gerbers(self):
        """
        Write gerber files, fiducial locations and drills
        :return:
        """
        self.logger.info("== Generating frame gerbers ==")

        _y_offset = (float(self.config["PanelOptions"]["panel_width"]) / 2)
        # Absolute coords for fiducial marks
        self.fid_coords = [
            (round(self.fid_points[0], 6), round(_y_offset, 6)),
            (round(self.panel_info["width"] + self.fid_points[1], 6), round(_y_offset, 6)),
            (round(self.fid_points[2], 6), round(self.panel_info["height"] - _y_offset, 6)),
            (round(self.panel_info["width"] + self.fid_points[3], 6), round(self.panel_info["height"] - _y_offset, 6))
        ]
        self.logger.debug("Fiducial coords: {}".format(self.fid_coords))

        # Absolute coords for corner drills
        self.drill_coords = [
            (round(_y_offset, 6), round(_y_offset, 6)),
            (round(self.panel_info["width"] - _y_offset, 6), round(_y_offset, 6)),
            (round(_y_offset, 6), round(self.panel_info["height"] - _y_offset, 6)),
            (round(self.panel_info["width"] - _y_offset, 6), round(self.panel_info["height"] - _y_offset, 6))
        ]
        self.logger.debug("Drill coords: {}".format(self.drill_coords))

        # Get file names from config file
        _file_names = self.config["GerberFilenames"]

        # Top and bottom copper have the same content, top and bottom fiducials
        _files = [self.out_path / _file_names["top_copper"], self.out_path / _file_names["bottom_copper"]]
        for _file in _files:
            self.file_list.append(_file)
            self.logger.debug("Writing gerber file: {}".format(_file.name))
            with open(_file, 'w') as out_file:
                out_file.writelines(gerber_header.format(_file.stem))

                out_file.write("G01*\n")
                out_file.write("%ADD10C,{:.6f}*%\n".format(float(self.fid_dia)))
                out_file.write("\n")
                out_file.write("D10*\n")

                for loc in self.fid_coords:
                    out_file.write("X{}Y{}D03*\n".format(int(loc[0] * 10000), int(loc[1] * 10000)))

                out_file.write("M02*\n")

        # Top and bottom soldermask layers have the same content, fiducials and mask for drills
        _files = [self.out_path / _file_names["top_soldermask"], self.out_path / _file_names["bottom_soldermask"]]
        for _file in _files:
            self.file_list.append(_file)
            self.logger.debug("Writing gerber file: {}".format(_file.name))
            with open(_file, 'w') as out_file:
                out_file.writelines(gerber_header.format(_file.stem))

                out_file.write("G01*\n")
                out_file.write("%ADD10C,{:.6f}*%\n".format(float(self.fid_soldermask_dia)))
                out_file.write("%ADD11C,3.203200*%\n")
                out_file.write("\n")

                out_file.write("D10*\n")
                for loc in self.fid_coords:
                    out_file.write("X{}Y{}D03*\n".format(int(loc[0] * 10000), int(loc[1] * 10000)))

                out_file.write("D11*\n")
                for loc in self.drill_coords:
                    out_file.write("X{}Y{}D03*\n".format(int(loc[0] * 10000), int(loc[1] * 10000)))

                out_file.write("M02*\n")

        # Make silkscreen layers
        self._load_font()
        _font_height = 1
        _file = self.out_path / _file_names["top_silkscreen"]
        self.file_list.append(_file)
        self.logger.debug("Writing gerber file: {}".format(_file.name))
        with open(_file, 'w') as out_file:
            out_file.writelines(gerber_header.format(_file.stem))

            out_file.write("G01*\n")
            _text_aperture = (self.text_size * (self.text_ratio / 100)) - 0.004
            out_file.write("%ADD10C,{}*%\n".format(_text_aperture))
            out_file.write("\n")
            out_file.write("D10*\n")

        # Repeat and Step x coords are determined dynamically based on text size
        text_locations = {
            "title": {"pos": [25.4, 5.3 - (self.text_size / 2)],
                      "string": self.panel_info["title"]
                      },
            "date": {"pos": [25.4, 2.6 - (self.text_size / 2)],
                     "string": datetime.datetime.now().strftime("%d/%b/%Y")
                     },
            "repeat": {"pos": [0, 5.3 - (self.text_size / 2)],
                       "string": "Repeat: {} x {}".format(self.panel_info["repeat"][0], self.panel_info["repeat"][1])
                       },
            "step": {"pos": [0, 2.6 - (self.text_size / 2)],
                     "string": "Step: {}mm x {}mm".format(round(self.panel_info["step"][0], 4), round(self.panel_info["step"][1], 4))
                     }
        }

        # Title and Date are written first, get the length of those
        _title_len = self._text_to_silk_mm(text_locations["title"]["string"])
        _date_len = self._text_to_silk_mm(text_locations["date"]["string"])
        _repeat_len = self._text_to_silk_mm(text_locations["repeat"]["string"])
        _step_len = self._text_to_silk_mm(text_locations["step"]["string"])

        # Add offset just calculated to the base location for the text
        _text_x_offset = max(_title_len, _date_len) + 5
        _base_x = text_locations["title"]["pos"][0]
        text_locations["repeat"]["pos"][0] = _base_x + _text_x_offset
        text_locations["step"]["pos"][0] = _base_x + _text_x_offset

        # Find if the strings to be drawn are going to end up off the PCB
        # Issue a warning to the user and ask for their input if this is the case
        _max_text_x = max((_repeat_len + text_locations["repeat"]["pos"][0]), (_step_len + text_locations["step"]["pos"][0]))
        self.logger.debug("Max silk X: {}".format(_max_text_x))

        _output_silk_layers = 1
        if _max_text_x > (self.fid_coords[1][0] - (self.fid_soldermask_dia / 2)):
            self.logger.warning("Silkscreen text on panel frame will extend beyond the edge of the panel")
            self.logger.warning("Do you still want to output the silkscreen layer?")
            self.logger.warning("The step and repeat information will still be output in the report file")
            _output_silk_input = input("Ouput panel silkscreen: (*Y/N)") or "Y"

            if _output_silk_input == "N":
                _output_silk_layers = 0
                self.logger.info("Skipping silkscreen layer output")
            elif _output_silk_input == "Y":
                pass
            else:
                self.logger.warning("input '{}' not recognised, assuming 'Y'".format(_output_silk_layers))

        if _output_silk_layers:
            self.logger.info("Outputting silkscreen layers")
            for text, value in text_locations.items():
                x_start = value["pos"][0]
                y_start = value["pos"][1]
                _string = value["string"]

                self._add_text_to_silk_file(_string, _file, x_start, y_start)

            if self.config["Fabrication"]["add_order_number_placeholder"].lower() == 'true':
                _placeholder = self.config["Fabrication"]["order_number_placeholder_text"]
                _placeholder_xstart = (self.panel_info["width"] / 2) - self._text_to_silk_mm(_placeholder)
                _placeholder_ystart = self.panel_info["height"] - (float(self.config["PanelOptions"]["panel_width"]) / 2) - (self.text_size / 2)

                self._add_text_to_silk_file(_placeholder, _file, _placeholder_xstart, _placeholder_ystart)

        with open(_file, 'a') as out_file:
            out_file.write("M02*\n")

        # Write excellon drill file
        _file = self.out_path / _file_names["drills"]
        self.file_list.append(_file)
        self.logger.debug("Writing drill file: {}".format(_file.name))
        with open(_file, 'w') as out_file:
            out_file.writelines(excellon_header)
            out_file.write("T1C{:.3f}\n".format(self.drill_dia))
            out_file.write("%\n")

            out_file.write("G90\n")
            out_file.write("M71\n")
            out_file.write("T1\n")

            for loc in self.drill_coords:
                out_file.write("X{}Y{}\n".format(int(loc[0] * 1000), int(loc[1] * 1000)))

            out_file.write("M30\n")

        # Make blank profile file
        _file = self.out_path / _file_names["profile"]
        self.file_list.append(_file)
        self.logger.debug("Writing profile file: {}".format(_file.name))
        with open(_file, 'w') as out_file:
            out_file.writelines(gerber_header)
            out_file.write("G01*\n")
            out_file.write("M02*\n")

    def _get_report_data(self):
        """
        Return some data to build reports
        fiducial data coords and drill location coords
        :return:
        """
        _data = {
            "fiducial_locations": self.fid_coords,
            "drill_locations": self.drill_coords
        }

        return _data

    def make_frame_gerbers(self, panel_dims, pcb_step, pcb_repeat, frame_title, output_directory, frame_config):
        """
        Generate a set of gerbers to place on the outer frame of the panel, contains fiducials and text
        :param panel_dims: A tuple containing (width, height) of the overall panel
        :param pcb_step: A tuple (step_x, step_y)
        :param pcb_repeat: A tuple (repeat_x, repeat_y)
        :param frame_title: Title of the panel, printed on the frame
        :param output_directory: A Path() object that specifies where the original gerber files are located
        :param frame_config: Configparser object containing read "config.ini" file
        :return:
        """
        self.out_path = Path(output_directory) / "_paneliser_temp_gerbers"
        self.logger.debug("Panel gerber output dir: {}".format(self.out_path))

        self.panel_info["width"] = panel_dims[0]
        self.panel_info["height"] = panel_dims[1]
        self.panel_info["step"] = pcb_step
        self.panel_info["repeat"] = pcb_repeat
        self.panel_info["title"] = frame_title
        self.config = frame_config

        self._make_output_dir()
        self._write_gerbers()

        _data = self._get_report_data()
        # _data["gerber_location"] = self._zip_output_dir()
        #
        # self._clean_output_dir()
        self.logger.info("= Finished writing frame gerbers =")

        return _data


if __name__ == '__main__':
    _config_path = Path.cwd() / "config.ini"
    config = ConfigParser()
    config.read(_config_path)
    # Testing dimensions, in mm
    app = GerberGenerator()
    app.make_frame_gerbers((100, 100), (5, 4), (25, 25), "Test. 12 34.0", Path.cwd(), config)
