## GerberPaneliserPaneliser
[![Python 3.6](https://img.shields.io/badge/python-3.6-blue.svg)](https://www.python.org/downloads/release/python-360/)

A simple file pre-processor (paneliser) to generate `.gerberset` file for use with 
[GerberPanelizer](https://github.com/ThisIsNotRocketScience/GerberTools).

This script takes a single zip file that has the correct format for use
with GerberPanelizer, asks for how many files to repeat along the X and
the Y direction and then generates the `.gerberset` file in the directory
that the zip file came from. This also creates a directory called 'panel'
in the same directory as the zip for GerberPanelizer to place the merged
files into.

There is a `config.ini` file that holds some settings that can be changed
the defualts will be applicable to most board houses though.

## Running
This script is written in python 3, it uses mostly builtin modules but there are some extras that need installing

First install requirements with `pip install -r requirements.txt`

Then run with `./main.py`

You may need to set the permissions with `sudo chmod +x main.py`

The script will ask you for some information, when it has completed there will be a folder called 'panel' in
the directory that the original gerber files are located, in this is the `.gerberset` file for use with
GerberPanelizer, a report.txt containing useful information when sending the panel for manufacture and for
when setting up smt machines. It also contains the directory where the panellised gerbers will be output to
