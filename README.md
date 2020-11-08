## GerberPaneliserPaneliser

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
