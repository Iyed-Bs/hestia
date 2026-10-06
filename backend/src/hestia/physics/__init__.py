"""
Physics shared by the live twin, the simulator and the planner.

One model per piece of equipment, each with its source, so the three tools
can never disagree about how much hydrogen a kilowatt-hour makes or how hot
a building gets. Units are SI unless a name says otherwise (_c, _bar, _kwh).

    solar.py        sun position, tilted irradiance, PV output
    electrolyser.py alkaline stack: voltage, Faraday efficiency, H₂ and heat
    electrolyte.py  KOH solution: concentration, density, conductivity
    storage.py      hydrogen tank: real-gas pressure and safety margins
    conversion.py   fuel cell, hydrogen boiler, heat pump
    building.py     one-zone thermal model with heating and cooling
"""
