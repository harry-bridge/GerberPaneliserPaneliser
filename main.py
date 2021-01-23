#! /usr/bin/env python3

import gerber
import shutil
import logzero
import logging
import math
import datetime
from pathlib import Path, PureWindowsPath
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from configparser import ConfigParser

from gerber_gen import GerberGenerator


class Panel:
    """
    Panelises a single gerber file into an array with mousebites, output it as an xml file that can
    be loaded straight into gerber panelizer so it can merged into one file.
    """
    _version = 1.7

    temp_path = Path.cwd() / "temp"

    config_file_path = Path.cwd() / "config.ini"
    config = ConfigParser()

    logger = None
    # Path where the user inputted gerber file is
    gerber_file_path = None
    # Top level output directory
    out_path = None

    # {size_x, size_y, surface_area, origin_x, origin_y}
    pcb_info = dict()
    # {width, height, surface_area, repeat_x, repeat_y, step_x, step_y, title}
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
    mousebite_alignments = {"c": {"name": "center", "translation": 0}, "l": {"name": "left", "translation": -0.8},
                            "r": {"name": "right", "translation": 0.8}, "x": {"name": "left 1/3", "translation": -0.5},
                            "v": {"name": "right 1/3", "translation": 0.5}}
    # list of tuples of x, y locations for each mousebite locations
    mousebite_coords = list()

    # Options that are used a lot, taken from the config file
    route_diameter = None
    decimal_precision = None
    mousebite_diameter = None
    # Max dimensions before a warning is generated
    max_panel_dimensions = None
    # Manufacturers maximum buildable dimensions
    _manf_max_panel_dimensions = None
    panel_frame_width = None
    profile_file_extensions = None

    max_panel_surface_area = None
    # If the gerber file contains any of these then ignore it
    ignored_file_starts = ['._', '.DS_Store']

    # GerberGenerator class object
    gerber_gen = None
    # panel_frame_gerber_dir = None
    # {fid_locations, drill_locations, fid_to_board_0_locations}
    panel_frame_info = dict()

    def __init__(self):
        self.logger = logzero.logger
        # logzero.loglevel(logging.DEBUG)
        logzero.loglevel(logging.INFO)

        # make sure the temp directory is valid, if not, create it
        if not self.temp_path.exists() or not self.temp_path.is_dir():
            Path.mkdir(self.temp_path)

        # Init the gerber generator
        self.gerber_gen = GerberGenerator(self.logger)

    def _read_config(self):
        """
        Parse the config file and make available to the rest of the program
        :return:
        """
        if not self.config_file_path.exists():
            return self._exit_error("Config file not found, please make sure it is located at: {}".format(self.config_file_path))

        self.config.read(self.config_file_path)
        self.logger.debug("Config sections: {}".format(self.config.sections()))

        _panel_options = self.config["PanelOptions"]
        self.route_diameter = float(_panel_options["route_diameter"])
        self.decimal_precision = int(_panel_options["decimal_precision"])
        self.mousebite_diameter = float(_panel_options["mousebite_diameter"])
        self.panel_frame_width = float(_panel_options["panel_width"])

        self.profile_file_extensions = _panel_options["profile_file_extension"].replace(' ', '').split(',')

        # Config file stores everything as strings
        _max_dims = _panel_options["max_panel_dimensions"].replace(' ', '').split(',')
        self.max_panel_dimensions = [float(x) for x in _max_dims]

        _fab_options = self.config["Fabrication"]
        self.max_panel_surface_area = float(_fab_options["max_panel_surface_area"])
        _max_dims = _fab_options["max_panel_dimensions"].replace(' ', '').split(',')
        self._manf_max_panel_dimensions = [float(x) for x in _max_dims]

    def _make_output_dir(self):
        """
        Makes various output directories for generated files
        output structure is the directory where the gerber zip is
           |-- panel
              report.txt
              panel.gerberset
              panel_frame_overlay.zip
              |-- panellised_gerbers
                 various gerber files
        :return:
        """
        self.out_path = self.gerber_file_path.parent / "panel"
        if not self.out_path.exists():
            self.out_path.mkdir()

        _panel_path = self.out_path / "panellised_gerbers"
        if not _panel_path.exists():
            _panel_path.mkdir()

    def _load_file(self):
        """
        Loads a single file to be turned into an array
        Input should be a zipfile with all the layers included in it
        :return:
        """
        self.logger.info("Please input path to gerber file")
        self.gerber_file_path = Path(input("File: ").strip().replace("\\", ""))
        # self.gerber_file_path = Path(self._temp_path)
        _found_profile_file = None

        self.logger.info("Loading file: {}".format(self.gerber_file_path))

        if self.gerber_file_path.suffix == ".zip":
            with ZipFile(self.gerber_file_path, 'r') as zip_file:
                for file in zip_file.namelist():
                    _file_name = Path(file).name

                    # First check that the file is not in the ignored list
                    if True not in [part.startswith(_file_name) for part in self.ignored_file_starts]:
                        self.logger.debug("File from zip archive: {}".format(_file_name))
                        if True in [ext in _file_name for ext in self.profile_file_extensions]:
                            # Got a profile file, now we can have a look at the max bounds of the file
                            self.logger.debug("Found a profile file")
                            self.logger.info("Profile file name: {}".format(_file_name))
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
            elif _current_units == "inch":
                self.logger.info("PCB units are imperial, converting to metric")
                read_pcb.to_metric()

            # bounds is a tuple of the form ((min_x, max_x), (min_y, max_y))
            pcb_bounds = read_pcb.bounds

            self.pcb_info["size_x"] = round(pcb_bounds[0][1] - pcb_bounds[0][0], 6)
            self.pcb_info["size_y"] = round(pcb_bounds[1][1] - pcb_bounds[1][0], 6)
            # Work out surface area in dm2
            _surface_area = (self.pcb_info["size_x"] * self.pcb_info["size_y"]) / 10000
            self.pcb_info["surface_area"] = round(_surface_area, 6)

            # origin is how far away the bottom left corner of the pcb is to the 'origin' of the board
            # need to flip the sign to get the coord of the origin wrt the bl corner
            self.pcb_info["origin_x"] = round(pcb_bounds[0][0] * -1, 6)
            self.pcb_info["origin_y"] = round(pcb_bounds[1][0] * -1, 6)

            self.logger.info("PCB info: {}".format(self.pcb_info))

        else:
            self._exit_error("No profile file found in zip, does it have the extension .gko?")

    def _make_mousebite_primitive_array(self, mousebite_list):
        """
        Takes in a list of mousebite locations and works out the relative coords of them in relation to the PCB
        offsets are calculated in relation to the PCB origin
        :param mousebite_list:
        :return: list of tuples of x, y locations for the relative coords
        """
        self.logger.debug("Building mousebite primitive array")
        _primitive_array = list()

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

            _mousebite_x_distance = (self.pcb_info['size_x'] / 2) + (self.route_diameter / 2)
            _mousebite_y_distance = (self.pcb_info['size_y'] / 2) + (self.route_diameter / 2)

            # Adjustment of the mousebite in the x and y direction to compensate for the mousebite 'diameter' on the edge of boards
            _mousebite_adjustment = [0, 0]
            # Small extra adjustment to move the mousebite away from corners
            # This also takes into  account the radius
            _extra_adjustment_for_bite = 1.2

            _size_key = None
            _alignment_index = None
            _direction = None

            # first translation is in the x direction
            if _location[0] != 0:
                _size_key = "size_y"
                _alignment_index = 1
                _direction = "Y"

            # first translation is in the y direction
            elif _location[1] != 0:
                _size_key = "size_x"
                _alignment_index = 0
                _direction = "X"

            ## Work out where to place the mousebite depending on whether the we need to shift in the X or Y direction
            # Consider only positive direction
            _center_to_bite_edge = (abs(_alignment) * (self.pcb_info[_size_key] / 2)) + self.mousebite_diameter
            # Convert to actual direction of the mousebite
            _center_to_bite_edge *= self._get_sign(_alignment)
            self.logger.debug("Center to bite edge {}: {}".format(_direction, round(_center_to_bite_edge, 6)))

            if abs(_center_to_bite_edge) > (self.pcb_info[_size_key] / 2):
                # Mousebite will end up off the edge of the PCB to move it in by the diameter of the bite
                # Add a little bit to the diameter so we end up out the way of any small corner radii
                _mousebite_adjustment[_alignment_index] = (self.pcb_info[_size_key] / 2) - (self.mousebite_diameter + _extra_adjustment_for_bite)

            else:
                # Mousebite will end up inside the pcb, so take off half the diameter from the dimension
                _mousebite_adjustment[_alignment_index] = abs(_center_to_bite_edge) - (self.mousebite_diameter / 2) - _extra_adjustment_for_bite

            # Change the sign so the direction is correct
            _mousebite_adjustment[_alignment_index] *= self._get_sign(_alignment)
            self.logger.debug("Mousebite {} adjustment: {}".format(_direction, round(_mousebite_adjustment[_alignment_index], 6)))

            ## Combine the calculated mousebite adjustment with the unit vector to produce a location on the PCB bounds
            # Convert the unit vector location to a location on the PCB bounding box
            _x_vector = (_location[0] * _mousebite_x_distance) + _mousebite_adjustment[0]
            _y_vector = (_location[1] * _mousebite_y_distance) + _mousebite_adjustment[1]
            self.logger.debug("Mousebite location on pcb: ({}, {})".format(round(_x_vector, 6), round(_y_vector, 6)))

            # Find the offset from the origin of the PCB to the center of the PCB
            _x_origin_to_center = (self.pcb_info['size_x'] / 2) - self.pcb_info['origin_x']
            _y_origin_to_center = (self.pcb_info['size_y'] / 2) - self.pcb_info['origin_y']

            # Round primitive components
            _primitive_x = round(_x_vector + _x_origin_to_center, 6)
            _primitive_y = round(_y_vector + _y_origin_to_center, 6)
            # Append vector tuple to array
            _primitive_array.append((_primitive_x, _primitive_y))

        self.logger.debug("Primitive array: {}".format(_primitive_array))
        return _primitive_array

    def _check_panel_dims(self):
        """
        Checks the overall panel size is within certain bounds and displays warnings if not
        1. Checks the panel is within the dimensions of your machines
        2. Checks the surface area is withing manufacturer limits (if warning enabled)
        3. Checks the panel is withing the manufacturer maximum dimensions
        :param width: Width of the panel in mm
        :param height: Height  of the panel in mm
        :return:
        """
        _warning_index = 0

        # Display a warning to the user if the dimensions will be outside the max dims in any orientation
        if not (self.panel_info["width"] <= self.max_panel_dimensions[0] and self.panel_info["height"] <=
                self.max_panel_dimensions[1]) and not \
                (self.panel_info["width"] <= self.max_panel_dimensions[1] and self.panel_info["height"] <=
                 self.max_panel_dimensions[0]):
            _warning_index += 1
            self.logger.warning("[#{}] Panel size is larger than max defined in config".format(_warning_index))
            self.logger.warning("Max panel dimensions: {}mm x {}mm".format(self.max_panel_dimensions[0],
                                                                           self.max_panel_dimensions[1]))

        if (self.panel_info["surface_area"] > self.max_panel_surface_area) and \
                self.config["Fabrication"]["show_surface_area_warning"].lower() == 'true':
            _warning_index += 1
            self.logger.warning("[#{}] Panel surface area is larger than max defined in config".format(_warning_index))
            self.logger.warning("Max panel surface area: {}dm2".format(self.max_panel_surface_area))

        # Display a warning to the user if the dimensions will be outside the max dims for the manufacturer in any orientation
        if not (self.panel_info["width"] <= self._manf_max_panel_dimensions[0] and self.panel_info["height"] <=
                self._manf_max_panel_dimensions[1]) and not \
                (self.panel_info["width"] <= self._manf_max_panel_dimensions[1] and self.panel_info["height"] <=
                 self._manf_max_panel_dimensions[0]):
            _warning_index += 1
            self.logger.warning("[#{}] Panel size is larger than manufacturer max".format(_warning_index))
            self.logger.warning("Max manufacturer dimensions: {}mm x {}mm".format(self._manf_max_panel_dimensions[0],
                                                                                  self._manf_max_panel_dimensions[1]))

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

        self.logger.info("= Title =")
        self.logger.info("Input title for panel frame")
        _default_title = self.gerber_file_path.stem.replace("_", " ")
        self.logger.info("Default: {}".format(_default_title))

        self.panel_info["title"] = input("Title: ").strip() or _default_title
        self.logger.debug("Title for frame: {}".format(self.panel_info["title"]))

        # Get the user to enter the desired step in the X and Y direction for the panel
        while 1:
            self.logger.info("= Repeat =")
            self.logger.info("How many boards to arrange in the X and Y directions")
            _x_repeat = self._try_int(input("X Repeat: "))
            if _x_repeat < 1:
                self.logger.error("X repeat must be greater or equal to 1")
                continue

            _y_repeat = self._try_int(input("Y Repeat: "))
            if _y_repeat < 1:
                self.logger.error("Y repeat must be greater or equal to 1")
                continue

            self.panel_info["width"] = round(self.panel_frame_width + self.route_diameter +
                                             ((self.pcb_info['size_x'] + self.route_diameter) * float(_x_repeat)) +
                                             self.panel_frame_width,
                                             6)

            self.panel_info["height"] = round(self.panel_frame_width + self.route_diameter +
                                              ((self.pcb_info['size_y'] + self.route_diameter) * float(_y_repeat)) +
                                              self.panel_frame_width,
                                              6)

            _surface_area = (self.panel_info["width"] * self.panel_info["height"]) / 10000
            self.panel_info["surface_area"] = round(_surface_area, 6)

            self.logger.info("Total number of PCBs in panel: {}".format(_x_repeat * _y_repeat))
            self.logger.info("Panel surface area: {}dm2".format(round(self.panel_info["surface_area"], 4)))
            self.logger.info("Panel Size: {}mm x {}mm".format(self.panel_info["width"], self.panel_info["height"]))

            # Display warnings if necessary
            self._check_panel_dims()

            _size_ok = input("Panel size acceptable? (*Y/N): ") or "Y"
            if _size_ok.upper() == "Y":
                break

        # Store panel info for report generation
        self.panel_info["repeat_x"] = _x_repeat
        self.panel_info["repeat_y"] = _y_repeat
        self.panel_info["step_x"] = self.pcb_info['size_x'] + self.route_diameter
        self.panel_info["step_y"] = self.pcb_info['size_y'] + self.route_diameter

        self.logger.info("")
        _horiz_bars_every = 0
        _vert_bars_every = 0

        while 1:
            self.logger.info("= Inter-board support bars =")
            self.logger.info("These are extra bits of panel in the X and/or Y direction that add support for odd shaped boards")

            _add_bars = input("Add inter-board support bars? (Y/*N): ") or "N"
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
                self.panel_info["step_x"] += float(self.config["PanelOptions"]["support_bar_width"]) + self.route_diameter

                # Issue a warning to the user if the maths doesn't quite work
                if ((_x_repeat - 1) % _vert_bars_every) != 0:
                    self.logger.warning("Chosen number of vertical support not easily divisible by the number of PCBs")
                    self.logger.warning("Support bars may not be placed evenly")

            if _horiz_bars_every != 0:
                self.logger.debug("Horizontal supports every: {}, total: {}".format(_horiz_bars_every, math.floor((_y_repeat - 1) / _horiz_bars_every)))
                _extra_height = math.floor((_y_repeat - 1) / _horiz_bars_every) * (float(self.config["PanelOptions"]["support_bar_width"]) + self.route_diameter)
                self.panel_info["height"] += _extra_height
                self.panel_info["step_y"] += float(self.config["PanelOptions"]["support_bar_width"]) + self.route_diameter

                # Issue a warning to the user if the maths doesn't quite work
                if ((_y_repeat - 1) % _horiz_bars_every) != 0:
                    self.logger.warning("Chosen number of vertical support not easily divisible by the number of PCBs")
                    self.logger.warning("Support bars may not be placed evenly")

            # Update the user on the new bounds of the panel
            if _horiz_bars_every != 0 or _vert_bars_every != 0:
                self.logger.info("New panel Size: {}mm x {}mm".format(self.panel_info["width"], self.panel_info["height"]))

                self._check_panel_dims()

                _size_ok = input("Panel size acceptable? (*Y/N): ") or "Y"
                if _size_ok.upper() == "Y":
                    break

        self.logger.info("= Mousebite locations =")
        self.logger.info("Locations are on a per board basis, any duplicate locations will be ignored")
        self.logger.info("Each location is 2 letters, the first being alignment, the second being location")
        self.logger.info("E.g. 'cb' will put a mousebite center-bottom,")
        self.logger.info("'cb,ct' will put mousebites at the center-bottom and the center-top")
        self.logger.info("Mousebite locations are not case sensitive, and are placed naively")
        self.logger.info("")

        # Dsiplay mousebite locations table
        self.logger.info("   tl  tx   tc  tv  tr   ")
        self.logger.info("lr ┌────────┬────────┐ rr")
        self.logger.info("   │                 │   ")
        self.logger.info("lv │                 │ rv")
        self.logger.info("   │                 │   ")
        self.logger.info("lc ├        ┼        ┤ rc")
        self.logger.info("   │                 │   ")
        self.logger.info("lx │                 │ rx")
        self.logger.info("   │                 │   ")
        self.logger.info("ll └────────┴────────┘ rl")
        self.logger.info("   bl  bx   bc  bv  br   ")

        self.logger.info("")
        self.logger.info("Mousebite locations list:")

        while 1:
            _mousebite_list = input("Locations: ")
            # _mousebite_list = ['bl']
            if len(_mousebite_list) > 0:
                _mousebite_list = _mousebite_list.replace(' ', '').split(',')
                break
            else:
                self.logger.warning("You must specify at least one mousebite")

        self.logger.debug("Locations list: {}".format(_mousebite_list))

        # Remove duplicates from the location list
        _mousebite_list = set(_mousebite_list)
        _mousebite_primitives = self._make_mousebite_primitive_array(_mousebite_list)
        _mousebite_coords = list()

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
                self.pbc_coords.append((round(_x_loc, 6), round(_y_loc, 6)))
                for bite in _mousebite_primitives:
                    _mousebite_coords.append((round(_x_loc + bite[0], 6), round(_y_loc + bite[1], 6)))

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
        self.mousebite_coords = set(_mousebite_coords)
        self.logger.debug("Mousebite Coords: {}".format(self.mousebite_coords))

    def _make_frame_gerbers(self):
        """
        Make frame output gerbers to overlay on the panel frame
        :return:
        """
        self.logger.info("== Making panel frame overlay gerbers ==")
        _panel_dims = (self.panel_info["width"], self.panel_info["height"])
        _panel_step = (self.panel_info["step_x"], self.panel_info["step_y"])
        _panel_repeat = (self.panel_info["repeat_x"], self.panel_info["repeat_y"])
        _frame_title = self.panel_info["title"]
        _output_dir = self.out_path

        _data = self.gerber_gen.make_frame_gerbers(_panel_dims, _panel_step, _panel_repeat, _frame_title, _output_dir, self.config)

        # Returned data is a dict containing fid locations, drill locations and the location of the output zip
        self.panel_frame_gerber_dir = _data["gerber_location"]
        self.panel_frame_info["fiducial_locations"] = _data["fiducial_locations"]
        self.panel_frame_info["drill_locations"] = _data["drill_locations"]

        _panel_to_board_offset = self.panel_frame_width + self.route_diameter
        _fids_to_board_0 = list()
        for _loc in self.panel_frame_info["fiducial_locations"]:
            # Fiducial locations are a tuple of (x, y) locations
            # Calculate the location from each of the fiducials to the origin of the first board
            _fids_to_board_0.append(
                (
                    round(_loc[0] - (self.pcb_info["origin_x"] + _panel_to_board_offset), 4),
                    round(_loc[1] - (self.pcb_info["origin_y"] + _panel_to_board_offset), 4)
                )
            )

        self.panel_frame_info["fid_to_board_0_locations"] = _fids_to_board_0
        self.logger.debug("Fids to first board: {}".format(_fids_to_board_0))

    def _write_xml(self):
        """
        Writes the .gerberset file for processing with panelizer
        GP abbreviation = GerberPanelizer
        :return:
        """
        root = ET.Element("GerberLayoutSet", {"xmlns:xsd": "http://www.w3.org/2001/XMLSchema", "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"})
        loaded_outlines = ET.SubElement(root, "LoadedOutlines")

        _loaded_outlines = {
            str(self.panel_frame_gerber_dir): [(0, 0)],
            str(self.gerber_file_path): self.pbc_coords
        }

        # Tell GP where the gerber zip file is
        ET.SubElement(loaded_outlines, "string").text = str(PureWindowsPath(self.gerber_file_path))
        ET.SubElement(loaded_outlines, "string").text = str(PureWindowsPath(self.panel_frame_gerber_dir))
        instances = ET.SubElement(root, "Instances")

        for _gerber_path, _gerber_coords in _loaded_outlines.items():
            for _loc in _gerber_coords:
                gerber_instance = ET.SubElement(instances, "GerberInstance")
                center = ET.SubElement(gerber_instance, "Center")
                # X and Y location of each thing
                ET.SubElement(center, "X").text = str(round(_loc[0], self.decimal_precision))
                ET.SubElement(center, "Y").text = str(round(_loc[1], self.decimal_precision))
                # Gerber rotation angle = 0
                ET.SubElement(gerber_instance, "Angle").text = str(0)
                # Tell GP which gerber file this is for
                ET.SubElement(gerber_instance, "GerberPath").text = str(PureWindowsPath(_gerber_path))
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
        ET.SubElement(root, "FillOffset").text = str(self.route_diameter)
        ET.SubElement(root, "Smoothing").text = str(0.5)
        ET.SubElement(root, "ExtraTabDrillDistance").text = str(0)
        # This can sometimes cause issues if the silk layer is over the edge of the board
        ET.SubElement(root, "ClipToOutlines").text = "true"

        # make last export folder
        # _export_dir = self.gerber_file_path.parent / self.config['PanelOptions']['default_export_folder_name']
        # if not _export_dir.exists() or not _export_dir.is_dir():
        #     _export_dir.mkdir()

        # Last export folder, already taken care on in _make_output_dir() function
        _panel_path = self.out_path / "panellised_gerbers"
        ET.SubElement(root, "LastExportFolder").text = str(PureWindowsPath(_panel_path))
        ET.SubElement(root, "DoNotGenerateMouseBites").text = "false"

        out_string = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ", newl="\n", encoding='utf-8')

        _out_path = self.out_path / (self.gerber_file_path.stem + "-panel.gerberset")
        with open(_out_path, 'wb') as out:
            out.write(out_string)

        self.logger.info("")
        self.logger.info("============== Success ==============")
        self.logger.info("== Gerberset written successfully! ==")
        self.logger.info("File is located at: {}".format(str(_out_path)))

    def _write_report(self):
        """
        Writes a report file to help with ordering the panel
        :return:
        """
        self.logger.info("== Writing panel generation report ==")

        _out_path = self.out_path / (self.gerber_file_path.stem + "-report.txt")
        with open(_out_path, 'w', newline="\r\n") as out:
            out.write("=" * 40 + "\n")
            out.write("GerberPanelizer Paneliser - V{}\n".format(self._version))
            out.write("Panel file generation report for: {}\n".format(self.panel_info["title"]))
            out.write("File generated on: {} at {}\n".format(
                datetime.datetime.now().strftime("%d/%b/%Y"),
                datetime.datetime.now().strftime("%H:%M")
            ))
            out.write("Gerberset path: {}\n".format(str(self.gerber_file_path.parent / (self.gerber_file_path.stem + "-panel.gerberset"))))
            out.write("=" * 40 + "\n")
            out.write("\n")

            out.write("Total number of PCBs on panel: {}\n".format(self.panel_info["repeat_x"] * self.panel_info["repeat_y"]))
            out.write("Repeat (X*Y): {} x {}\n".format(self.panel_info["repeat_x"], self.panel_info["repeat_y"]))
            out.write("Step (X*Y): {}mm x {}mm\n".format(round(self.panel_info["step_x"], 4), round(self.panel_info["step_y"], 4)))
            out.write("\n")

            out.write("Panel size (W*H): {}mm x {}mm\n".format(round(self.panel_info["width"], 4), round(self.panel_info["height"], 4)))
            out.write("Panel surface area: {}dm2\n".format(round(self.panel_info["surface_area"], 4)))
            out.write("PCB size (X*Y): {}mm x {}mm\n".format(round(self.pcb_info["size_x"], 4), round(self.pcb_info["size_y"], 4)))
            out.write("PCB surface area: {}dm2\n".format(round(self.pcb_info["surface_area"], 4)))
            out.write("\n")

            out.write("== Panel Fiducials ==\n")
            _fids_order = ["BL", "BR", "TL", "TR"]
            # out.write("Fiducial locations (X, Y):\n")
            # for index, _loc in enumerate(self.panel_frame_info["fiducial_locations"]):
            #     out.write("  {} - {}\n".format(_fids_order[index], _loc))
            # out.write("\n")

            out.write("Fiducials to board 0 (X, Y)\n")
            for index, _loc in enumerate(self.panel_frame_info["fid_to_board_0_locations"]):
                out.write("  {} - {}\n".format(_fids_order[index], _loc))

    def _clean_tempfiles(self):
        """
        Cleans up the temp directory after finishing
        :return:
        """
        self.logger.info("== Cleaning up tempfiles ==")
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
        if message:
            self.logger.error(message)

        self.logger.error("Error Occurred, Quitting")

        self._clean_tempfiles()
        exit(-1)

    def on_execute(self):
        self.logger.info("== Gerber Paneliser Paneliser ==")

        self._read_config()
        self._load_file()
        self._make_output_dir()
        self._make_array()
        self._make_frame_gerbers()
        self._clean_tempfiles()
        self._write_report()
        self._write_xml()


if __name__ == '__main__':
    app = Panel()
    app.on_execute()
