#! /usr/bin/env python3

import gerber
import shutil
import logzero
import logging
import math
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from configparser import ConfigParser


class Panel:
    """
    Panelises a single gerber file into an array with mousebites, output it as an xml file that can
    be loaded straight into gerber panelizer so it can merged into one file.
    """
    temp_path = Path.cwd() / "temp"

    config_file_path = Path.cwd() / "config.ini"
    config = ConfigParser()

    logger = None
    gerber_file_path = None

    # {size_x, size_y, origin_x, origin_y}
    pcb_info = dict()
    # {width, height}
    panel_info = dict()

    # list of tuples of x, y locations for each pcb instance
    pbc_coords = list()

    # Possible mousebite locations around the PCB split up for easy mixing and matching
    # Locations are bottom, top, left and right, the name key is used as a description for the user
    # the translation key is a unit vector that represents the location from the center of the pcb bounding box
    # for the locations the unit vector must be an (x, y) tuple that translates in only one direction
    mousebite_locations = {"b": {"name": "bottom", "translation": (0, -1)}, "t": {"name": "top", "translation": (0, 1)},
                           "l": {"name": "left", "translation": (-1, 0)}, "r": {"name": "right", "translation": (1, 0)}}
    # the translation is a single unit direction for which way the alignment should go if it is imagined in the x direction only
    mousebite_alignments = {"c": {"name": "center", "translation": 0}, "l": {"name": "left", "translation": -1},
                            "r": {"name": "right", "translation": 1}, "v": {"name": "left 2/3", "translation": -0.6},
                            "x": {"name": "right 2/3", "translation": 0.6}}
    # list of tuples of x, y locations for each mousebite locations
    mousebite_coords = list()

    # Options that are used a lot, taken from the config file
    route_diameter = 0
    decimal_precision = 0
    mousebite_diameter = 0

    def __init__(self):
        self.logger = logzero.logger
        logzero.loglevel(logging.DEBUG)

        # make sure the temp directory is valid, if not, create it
        if not self.temp_path.exists() or not self.temp_path.is_dir():
            Path.mkdir(self.temp_path)

    def _read_config(self):
        """
        Parse the config file and make available to the rest of the program
        :return:
        """
        print(self.config_file_path)

        if not self.config_file_path.exists():
            return self._exit_error("Config file not found, please make sure it is located at: {}".format(self.config_file_path))

        self.config.read(self.config_file_path)
        self.logger.debug("Config sections: {}".format(self.config.sections()))

        _panel_options = self.config["PanelOptions"]
        self.route_diameter = float(_panel_options["route_diameter"])
        self.decimal_precision = int(_panel_options["decimal_precision"])
        self.mousebite_diameter = float(_panel_options["mousebite_diameter"])

    def _load_file(self):
        """
        Loads a single file to be turned into an array
        Input should be a zipfile with all the layers included in it
        :return:
        """
        self.logger.info("Please input path to gerber file")
        self.gerber_file_path = Path(input("File: ").strip().replace("\\ ", " "))
        # self.gerber_file_path = Path(self._temp_path)
        _found_profile_file = None

        self.logger.debug("Loading file: {}".format(self.gerber_file_path))

        if self.gerber_file_path.suffix == ".zip":
            with ZipFile(self.gerber_file_path, 'r') as zip_file:
                for file in zip_file.namelist():
                    self.logger.debug("File from zip archive: {}".format(file))
                    if '.gko' in file:
                        # Got a profile file, now we can have a look at the max bounds of the file
                        self.logger.debug("Found a profile file")
                        self.logger.info("Profile file name: {}".format(file))
                        _found_profile_file = file

                        # Extract profile file to temp dir
                        zip_file.extract(file, str(self.temp_path))
                        break

        else:
            self._exit_error("Can't load file, needs to be a .zip.")

        if _found_profile_file is not None:
            read_pcb = gerber.read(str(self.temp_path / _found_profile_file))

            # Check what units the gerber file is in
            _current_units = read_pcb.units
            if _current_units == "metric":
                self.logger.info("PCB units are metric, no conversion required")
            elif _current_units == "imperial":
                self.logger.info("PCB units are imperial, converting to metric")
                read_pcb.to_metric()

            # bounds is a tuple of the form ((min_x, max_x), (min_y, max_y))
            pcb_bounds = read_pcb.bounds

            self.pcb_info["size_x"] = pcb_bounds[0][1] - pcb_bounds[0][0]
            self.pcb_info["size_y"] = pcb_bounds[1][1] - pcb_bounds[1][0]

            # origin is how far away the bottom left corner of the pcb is to the 'origin' of the board
            # need to flip the sign to get the coord of the origin wrt the bl corner
            self.pcb_info["origin_x"] = pcb_bounds[0][0] * -1
            self.pcb_info["origin_y"] = pcb_bounds[1][0] * -1

            self.logger.info("PCB info: {}".format(self.pcb_info))

        else:
            self._exit_error("No profile file found in zip, does it have the extension .gko?")

    def _make_mousebite_primitave_array(self, mousebite_list):
        """
        Takes in a list of mousebite locations and works out the relative coords of them in relation to the PCB
        offsets are calculated in relation to the PCB origin
        :param mousebite_list:
        :return: list of tuples of x, y locations for the relative coords
        """
        self.logger.debug("Building mousebite primitive array")
        _primative_array = list()

        for location in mousebite_list:
            # loop through the list of locations that the user has entered
            # at this point the list has been de-duplicated
            _error = 0
            _location = None
            _alignment = None
            _unified_location = [0, 0]
            # self.logger.debug("Parsing mousebite location: {}".format(location))

            try:
                _location = self.mousebite_locations[location[0]]['translation']
            except KeyError:
                self.logger.debug("Key '{}' not found in location array".format(location[0]))
                _error = 1

            try:
                _alignment = self.mousebite_alignments[location[1]]['translation']
            except KeyError:
                _error = 1

            if _error == 1:
                self.logger.warning("Location {} is invalid, removing it from the list.".format(location))
                continue

            self.logger.debug("User entered location: {}".format(location))
            self.logger.debug("Location: {} - Alignment: {}".format(_location, _alignment))

            # protect location tuples from being edited
            _unified_location[0] = _location[0]
            _unified_location[1] = _location[1]

            _mousebite_x_distance = (self.pcb_info['size_x'] / 2) + (self.route_diameter / 2)
            _mousebite_y_distance = (self.pcb_info['size_y'] / 2) + (self.route_diameter / 2)

            # Adjustment of the mousebite in the x and y direction to compensate for the mousebite 'diameter' on the edge of boards
            _mousebite_adjustment = [0, 0]

            # first translation is in the x direction
            if _location[0] != 0:
                # Consider only positive direction
                _center_to_bite_edge = (abs(_alignment) * (self.pcb_info['size_y'] / 2)) + self.mousebite_diameter
                # Convert to actual direction of the mousebite
                _center_to_bite_edge *= self._get_sign(_alignment)
                self.logger.debug("Center to bite edge Y: {}".format(_center_to_bite_edge))

                if abs(_center_to_bite_edge) > (self.pcb_info['size_y'] / 2):
                    # Mousebite will end up off the edge of the PCB to move it in by the diameter of the bite
                    _mousebite_adjustment[1] = (self.pcb_info['size_y'] / 2) - self.mousebite_diameter
                    # Change the sign so the direction is correct
                    _mousebite_adjustment[1] *= self._get_sign(_alignment)
                else:
                    # Get whether the alignment is positive or negative
                    _mousebite_adjustment[1] = self._get_sign(_alignment) * _center_to_bite_edge

                self.logger.debug("Mousebite Y adjustment: {}".format(_mousebite_adjustment[0]))
                _unified_location[1] += _alignment

            # first translation is in the y direction
            if _location[1] != 0:
                # Consider only positive direction
                _center_to_bite_edge = (abs(_alignment) * (self.pcb_info['size_x'] / 2)) + self.mousebite_diameter
                # Convert to actual direction of the mousebite
                _center_to_bite_edge *= self._get_sign(_alignment)
                self.logger.debug("Center to bite edge X: {}".format(_center_to_bite_edge))

                if abs(_center_to_bite_edge) > (self.pcb_info['size_x'] / 2):
                    # Mousebite will end up off the edge of the PCB to move it in by the diameter of the bite
                    _mousebite_adjustment[0] = (self.pcb_info['size_x'] / 2) - self.mousebite_diameter
                    # Change the sign so the direction is correct
                    _mousebite_adjustment[0] *= self._get_sign(_alignment)
                else:
                    # Get whether the alignment is positive or negative
                    _mousebite_adjustment[0] = self._get_sign(_alignment) * _center_to_bite_edge

                self.logger.debug("Mousebite X adjustment: {}".format(_mousebite_adjustment[0]))
                _unified_location[0] += _alignment

            self.logger.debug("Unified location vector: {}".format(_location))

            # convert the unit vector location to a location on the PCB bounding box
            _x_vector = (_location[0] * _mousebite_x_distance) + _mousebite_adjustment[0]
            _y_vector = (_location[1] * _mousebite_y_distance) + _mousebite_adjustment[1]
            self.logger.debug("Mousebite location on pcb: ({}, {})".format(_x_vector, _y_vector))

            # Find the offset from the origin of the PCB to the center of the PCB
            _x_origin_to_center = (self.pcb_info['size_x'] / 2) - self.pcb_info['origin_x']
            _y_origin_to_center = (self.pcb_info['size_y'] / 2) - self.pcb_info['origin_y']

            # Append vector tuple to array
            _primative_array.append((_x_vector + _x_origin_to_center, _y_vector + _y_origin_to_center))

        self.logger.debug("Primative array: {}".format(_primative_array))
        return _primative_array

    def _make_array(self):
        """
        Make the array of boards
        First ask for the array info (repeat x and repeat y)
        Then ask for a list of mousebite locations
        :return:
        """
        _mousebite_list = list()

        self.logger.info("== Input information for array ==")
        self.logger.info("PCB Size: {}mm x {}mm".format(self.pcb_info['size_x'], self.pcb_info['size_y']))

        while 1:
            self.logger.info("= Repeat =")
            self.logger.info("How many boards to arrange in the X and Y directions")
            _x_repeat = self._try_int(input("X Repeat: "))
            _y_repeat = self._try_int(input("Y Repeat: "))

            self.panel_info["width"] = 10 + ((self.pcb_info['size_x'] + 2.0) * float(_x_repeat)) + 8
            self.panel_info["height"] = 10 + ((self.pcb_info['size_y'] + 2.0) * float(_y_repeat)) + 8

            self.logger.info("Panel Size: {}mm x {}mm".format(self.panel_info["width"], self.panel_info["height"]))

            _size_ok = input("Panel size acceptable? (Y/N): ") or "Y"
            if _size_ok.upper() == "Y":
                break

        self.logger.info("")
        _horiz_bars_every = 0
        _vert_bars_every = 0

        while 1:
            self.logger.info("= Inter-board support bars =")
            self.logger.info("These are extra bits of panel in the X and/or Y direction that add support for odd shaped boards")

            _add_bars = input("Add inter-board support bars? (Y/N): ") or "Y"
            if _add_bars.upper() == "N":
                break

            # User has selected to add support bars so make "Y" into a boolean variable for easier logic
            _add_bars = 1
            _input = input("Add horizontal support bars? (Y/N): ") or "N"
            if _input.upper() == "Y":
                _horiz_bars_every = input("Horizontal supports every Y PCBs: ")
                try:
                    _horiz_bars_every = int(_horiz_bars_every)
                except ValueError:
                    self.logger.error("{} is not an integer".format(_horiz_bars_every))
                    continue

                if _horiz_bars_every <= 0:
                    self.logger.error("Input needs to be greater than 0")
                    continue

            _input = input("Add vertical support bars? (Y/N): ") or "N"
            if _input.upper() == "Y":
                _vert_bars_every = input("Vertical supports every X PCBs: ")
                try:
                    _vert_bars_every = int(_vert_bars_every)
                except ValueError:
                    self.logger.error("{} is not an integer".format(_vert_bars_every))
                    continue

                if _vert_bars_every <= 0:
                    self.logger.error("Input needs to be greater than 0")
                    continue

            # need to update the bounds of the pcb maybe
            if _vert_bars_every != 0:
                # Fence post vs holes problem, need to take 1 from the repeat to get the number of holes in the pcb array
                self.logger.debug("Vertical supports every: {}, total: {}".format(_vert_bars_every, math.floor((_x_repeat - 1) / _vert_bars_every)))
                # Find out how many supports we need to add then multiply that by the extra height added by one support and one router width
                # The router width the other side of the support is already taken care of in the case of a normal array w/o supports
                _extra_width = math.floor((_x_repeat - 1) / _vert_bars_every) * (float(self.config["PanelOptions"]["support_bar_width"]) + self.route_diameter)
                self.panel_info["width"] += _extra_width

                # Issue a warning to the user if the maths doesn't quite work
                if ((_x_repeat - 1) % _vert_bars_every) != 0:
                    self.logger.warning("Chosen number of vertical support not easily divisible by the number of PCBs")
                    self.logger.warning("Support bars may not be placed evenly")

            if _horiz_bars_every != 0:
                self.logger.debug("Horizontal supports every: {}, total: {}".format(_horiz_bars_every, math.floor((_y_repeat - 1) / _horiz_bars_every)))
                _extra_height = math.floor((_y_repeat - 1) / _horiz_bars_every) * (float(self.config["PanelOptions"]["support_bar_width"]) + self.route_diameter)
                self.panel_info["height"] += _extra_height

                # Issue a warning to the user if the maths doesn't quite work
                if ((_y_repeat - 1) % _horiz_bars_every) != 0:
                    self.logger.warning("Chosen number of vertical support not easily divisible by the number of PCBs")
                    self.logger.warning("Support bars may not be placed evenly")

            # Update the user on the new bounds of the panel
            if _horiz_bars_every != 0 or _vert_bars_every != 0:
                self.logger.info("New panel Size: {}mm x {}mm".format(self.panel_info["width"], self.panel_info["height"]))

            _size_ok = input("Panel size acceptable? (Y/N): ") or "Y"
            if _size_ok.upper() == "Y":
                break

        self.logger.info("")
        self.logger.info("= Mousebite locations =")
        self.logger.info("Locations are on a per board basis, any duplicate locations will be ignored")
        self.logger.info("Each location is 2 letters, the first being alignment, the second being location")
        self.logger.info("E.g. 'cb' will put a mousebite center-bottom,")
        self.logger.info("'cb,ct' will put mousebites at the center-bottom and the center-top")
        self.logger.info("Mousebite locations are not case sensitive, and are placed naively")

        self.logger.info("")
        self.logger.info("Locations:")
        for key, value in self.mousebite_locations.items():
            self.logger.info("{}: {}".format(key.upper(), value["name"].title()))

        self.logger.info("")
        self.logger.info("Alignments:")
        for key, value in self.mousebite_alignments.items():
            self.logger.info("{}: {}".format(key.upper(), value["name"].title()))

        self.logger.info("")
        self.logger.info("Mousebite locations list:")
        _mousebite_list = input("Locations: ")
        _mousebite_list = _mousebite_list.replace(' ', '').split(',')
        # _mousebite_list = ['bl']

        self.logger.debug("Locations list: {}".format(_mousebite_list))

        # Remove duplicates from the location list
        _mousebite_list = set(_mousebite_list)
        _mousebite_primitives = self._make_mousebite_primitave_array(_mousebite_list)

        _x_start = float(self.config["PanelOptions"]["panel_width"]) + self.route_diameter + self.pcb_info['origin_x']
        _y_start = float(self.config["PanelOptions"]["panel_width"]) + self.route_diameter + self.pcb_info['origin_y']
        _x_loc = _x_start
        _y_loc = _y_start

        for y_index in range(_y_repeat):
            for x_index in range(_x_repeat):
                # make a list of all the x, y locations that the boards should be
                # board x, y need to take into account the gerber 'origin'
                # also make a list of all the x, y locations that the mousebites should be
                # mousebite x, y are located from the center of the mousebite
                self.pbc_coords.append((_x_loc, _y_loc))
                for bite in _mousebite_primitives:
                    self.mousebite_coords.append((_x_loc + bite[0], _y_loc + bite[1]))

                _x_loc += self.pcb_info['size_x'] + self.route_diameter

                # Add the the support bar width to the next x location if we need to
                # x_index will start at 0 so need to add 1 to get the intended result from the modulus function
                if (_vert_bars_every != 0) and ((x_index + 1) % _vert_bars_every == 0):
                    _x_loc += float(self.config["PanelOptions"]["support_bar_width"]) + self.route_diameter

            _x_loc = _x_start
            _y_loc += self.pcb_info['size_y'] + self.route_diameter

            # Add the support bar width to the next y location if we need to
            if (_horiz_bars_every != 0) and ((y_index + 1) % _horiz_bars_every == 0):
                _y_loc += float(self.config["PanelOptions"]["support_bar_width"]) + self.route_diameter

        self.logger.debug("PCB Coords: {}".format(self.pbc_coords))

        # Remove any duplicates from the mousebite coords array
        self.mousebite_coords = set(self.mousebite_coords)
        self.logger.debug("Mousebite Coords: {}".format(self.mousebite_coords))

    def _write_xml(self):
        """
        Writes the .gerberset file for processing with panelizer
        GP abbreviation = GerberPanelizer
        :return:
        """
        root = ET.Element("GerberLayoutSet", {"xmlns:xsd": "http://www.w3.org/2001/XMLSchema", "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"})
        loaded_outlines = ET.SubElement(root, "LoadedOutlines")

        # Tell GP where the gerber zip file is
        ET.SubElement(loaded_outlines, "string").text = str(self.gerber_file_path)
        instances = ET.SubElement(root, "Instances")

        for _loc in self.pbc_coords:
            gerber_instance = ET.SubElement(instances, "GerberInstance")
            center = ET.SubElement(gerber_instance, "Center")
            # X and Y location of each thing
            ET.SubElement(center, "X").text = str(round(_loc[0], self.decimal_precision))
            ET.SubElement(center, "Y").text = str(round(_loc[1], self.decimal_precision))
            # Gerber rotation angle = 0
            ET.SubElement(gerber_instance, "Angle").text = str(0)
            # Tell GP which gerber file this is for
            ET.SubElement(gerber_instance, "GerberPath").text = str(self.gerber_file_path)
            # File hasn't been generated
            ET.SubElement(gerber_instance, "Generated").text = "false"

        tabs = ET.SubElement(root, "Tabs")

        for _tab in self.mousebite_coords:
            breaktab = ET.SubElement(tabs, "BreakTab")
            center = ET.SubElement(breaktab, "Center")
            # X and Y location of each thing
            ET.SubElement(center, "X").text = str(round(_tab[0], self.decimal_precision))
            ET.SubElement(center, "Y").text = str(round(_tab[1], self.decimal_precision))
            # tab rotation angle = 0
            ET.SubElement(breaktab, "Angle").text = str(0)
            ET.SubElement(breaktab, "Radius").text = str(self.mousebite_diameter)
            # Don't know why the valid tag is always false, but it is
            ET.SubElement(breaktab, "Valid").text = "false"

        # EOF settings and configurations
        ET.SubElement(root, "Width").text = str(round(self.panel_info['width'], self.decimal_precision))
        ET.SubElement(root, "Height").text = str(round(self.panel_info['height'], self.decimal_precision))
        ET.SubElement(root, "MarginBetweenBoards").text = str(self.route_diameter)
        # Fill the outside of the board
        ET.SubElement(root, "ConstructNegativePolygon").text = "true"
        # There is an issue with odd sized boards where GP will think breaktabs are invalid sometimes
        ET.SubElement(root, "FillOffset").text = str(self.route_diameter + 0.01)
        ET.SubElement(root, "Smoothing").text = str(1)
        ET.SubElement(root, "ExtraTabDrillDistance").text = str(0)
        # This can sometimes cause issues if the silk layer is over the edge of the board
        ET.SubElement(root, "ClipToOutlines").text = "true"

        # make last export folder
        _export_dir = self.gerber_file_path.parent / self.config['PanelOptions']['default_export_folder_name']
        if not _export_dir.exists() or not _export_dir.is_dir():
            _export_dir.mkdir()

        ET.SubElement(root, "LastExportFolder").text = str(_export_dir)
        ET.SubElement(root, "DoNotGenerateMouseBites").text = "false"

        out_string = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ", newl="\n", encoding='utf-8')

        _out_path = self.gerber_file_path.parent / (self.gerber_file_path.stem + "-panel.gerberset")
        with open(_out_path, 'wb') as out:
            out.write(out_string)

        self.logger.info("Gerberset written successfully!")
        self.logger.info("File is located at: {}".format(str(_out_path)))

    def _clean_tempfiles(self):
        """
        Cleans up the temp directory after finishing
        :return:
        """
        self.logger.info("Cleaning up tempfiles")
        self.logger.debug("Tempfile directory: {}".format(str(self.temp_path)))

        shutil.rmtree(self.temp_path)

    def _try_int(self, _input):
        """
        Checks whether a user input can bed turned into an in, otherwise throws an error
        :param _input: user input that may be a number
        :return: int
        """

        try:
            _ret = int(_input)
            return _ret
        except ValueError:
            return self._exit_error("Input {} is not an integer".format(_input))

    @staticmethod
    def _get_sign(number):
        """
        works out the sign component of a given number
        :param number: negative or positive real number
        :return: -1 for negative and 1 for positive and 0 for 0
        """

        if number == 0:
            return 0
        if number < 0:
            return -1
        else:
            return 1

    def _exit_error(self, message=None):
        self.logger.error("Error Occurred, Quitting")
        if message:
            self.logger.error(message)

        exit(-1)

    def on_execute(self):
        self.logger.info("== Gerber Paneliser Paneliser ==")

        self._read_config()
        self._load_file()
        self._make_array()
        self._clean_tempfiles()
        self._write_xml()


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    app = Panel()
    app.on_execute()
