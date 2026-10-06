"""
Ready-made situations for the simulator: each one shows what the system
does in a case that matters, without waiting for the weather to bring it.

A scenario sets the date and hour (local time), the state of the storage and
the building, the faults, and the speed at which it is worth watching. Some
also change equipment (the hydrogen boiler, an islanded building). The
visitor's own settings are kept for everything a scenario does not touch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hestia.twin.faults import FaultName


@dataclass(frozen=True)
class Scenario:
    id: str
    group: str  # energy | comfort | safety | trust
    title_en: str
    title_fr: str
    story_en: str
    story_fr: str
    month: int
    day: int
    hour: float
    speed: float = 60.0
    config: dict[str, Any] = field(default_factory=dict)  # equipment changes
    tank_pct: float | None = None
    battery_pct: float | None = None
    indoor_c: float | None = None
    electrolyte_c: float | None = None
    faults: tuple[FaultName, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group": self.group,
            "title": {"en": self.title_en, "fr": self.title_fr},
            "story": {"en": self.story_en, "fr": self.story_fr},
            "when": {"month": self.month, "day": self.day, "hour": self.hour},
            "speed": self.speed,
            "faults": list(self.faults),
            "config": self.config,
        }


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "summer_day",
        "energy",
        "A summer day",
        "Une journée d'été",
        "Dawn in July. The sun fills the battery first (it gives back 92 % tonight), the grid takes "
        "its yearly share of the production, and the stack turns what neither can take into "
        "hydrogen for the winter, instead of throwing it away. Fast-forward to the evening.",
        "L'aube en juillet. Le soleil remplit d'abord la batterie (elle rend 92 % le soir), le réseau "
        "prend sa part annuelle de la production, et l'empilement transforme en hydrogène pour "
        "l'hiver ce qu'aucun des deux ne peut prendre, au lieu de le perdre.",
        7,
        10,
        5.5,
        speed=600,
        tank_pct=20,
        battery_pct=30,
    ),
    Scenario(
        "winter_evening",
        "comfort",
        "Winter evening on hydrogen",
        "Soirée d'hiver à l'hydrogène",
        "January, sunset, battery already low. The heat pump keeps the house at 20 °C and the fuel "
        "cell powers it from summer's hydrogen, its own heat going into the house too.",
        "Janvier, coucher du soleil, batterie déjà basse. La pompe à chaleur tient la maison à 20 °C "
        "et la pile à combustible l'alimente avec l'hydrogène de l'été, sa chaleur allant aussi à la maison.",
        1,
        15,
        17.0,
        speed=300,
        tank_pct=60,
        battery_pct=25,
        indoor_c=18.5,
    ),
    Scenario(
        "winter_boiler",
        "comfort",
        "The hydrogen boiler instead",
        "La chaudière à hydrogène à la place",
        "The same evening with a hydrogen boiler. It burns hydrogen only for the heat the heat pump "
        "could not make from the building's own energy: compare the hydrogen used with the fuel cell.",
        "La même soirée avec une chaudière à hydrogène. Elle ne brûle l'hydrogène que pour la chaleur "
        "que la pompe à chaleur ne pourrait pas produire avec l'énergie du bâtiment : comparez.",
        1,
        15,
        17.0,
        speed=300,
        config={"end_use": "boiler"},
        tank_pct=60,
        battery_pct=25,
        indoor_c=18.5,
    ),
    Scenario(
        "heat_wave",
        "comfort",
        "Heat wave",
        "Canicule",
        "Late July, 29 °C inside at noon. The heat pump cools the house while the stack keeps "
        "producing on the surplus, its cooling loop working hard in the hot air.",
        "Fin juillet, 29 °C à l'intérieur à midi. La pompe à chaleur rafraîchit la maison pendant que "
        "l'empilement produit sur le surplus, sa boucle de refroidissement travaillant dans l'air chaud.",
        7,
        28,
        12.0,
        speed=300,
        indoor_c=29.0,
        electrolyte_c=50.0,
    ),
    Scenario(
        "grid_outage",
        "energy",
        "Grid outage",
        "Coupure du réseau",
        "A winter evening and the grid goes down. The building runs on its own: battery first, then "
        "the fuel cell. Watch what is left unserved, if anything.",
        "Un soir d'hiver, le réseau tombe. Le bâtiment vit sur ses réserves : la batterie d'abord, puis "
        "la pile à combustible. Regardez ce qui reste non servi, s'il en reste.",
        1,
        20,
        17.5,
        speed=300,
        tank_pct=50,
        battery_pct=60,
        indoor_c=19.5,
        faults=("grid_outage",),
    ),
    Scenario(
        "full_tank",
        "safety",
        "Storage at its limit",
        "Stockage à sa limite",
        "A hot August morning with the tank almost full. Pressure rises with the sun on the tank; "
        "production stops at 95 % of the maximum working pressure, before the relief valve.",
        "Un matin chaud d'août, réservoir presque plein. La pression monte avec le soleil sur le "
        "réservoir ; la production s'arrête à 95 % de la pression maximale, avant la soupape.",
        8,
        5,
        9.0,
        speed=300,
        tank_pct=92,
        battery_pct=90,
        electrolyte_c=50.0,
    ),
    Scenario(
        "small_leak",
        "safety",
        "A small leak",
        "Une petite fuite",
        "A fitting loosens: 2 L/min of hydrogen. Three readings above 500 ppm stop production and "
        "start the extraction fan, which holds the room far below the alarm. Remove the fault to "
        "watch the five-minute purge.",
        "Un raccord se desserre : 2 L/min d'hydrogène. Trois mesures au-dessus de 500 ppm arrêtent la "
        "production et lancent l'extraction, qui tient la pièce loin de l'alarme. Retirez la panne "
        "pour voir la purge de cinq minutes.",
        7,
        10,
        11.0,
        speed=5,
        battery_pct=90,
        electrolyte_c=50.0,
        faults=("leak_small",),
    ),
    Scenario(
        "large_leak",
        "safety",
        "A large leak",
        "Une grosse fuite",
        "A cracked line: 20 L/min. The room passes 2 000 ppm despite the fan; the alarm latches, the "
        "relay cuts the power, extraction keeps running. Only an operator with a written reason can "
        "reset it.",
        "Une conduite fissurée : 20 L/min. La pièce dépasse 2 000 ppm malgré l'extraction ; l'alarme "
        "se verrouille, le relais coupe l'alimentation, l'extraction continue. Seul un opérateur, avec "
        "un motif écrit, peut réarmer.",
        7,
        10,
        11.0,
        speed=5,
        battery_pct=90,
        electrolyte_c=50.0,
        faults=("leak_large",),
    ),
    Scenario(
        "fan_failure",
        "safety",
        "A leak with a dead fan",
        "Une fuite, ventilateur en panne",
        "The same small leak, but the extraction fan does not turn. With only natural ventilation the "
        "gas builds up until the second stage latches: why the fan is checked at every inspection.",
        "La même petite fuite, mais l'extracteur ne tourne pas. Avec la seule ventilation naturelle, le "
        "gaz s'accumule jusqu'au second seuil : c'est pourquoi l'extracteur est vérifié à chaque inspection.",
        7,
        10,
        11.0,
        speed=20,
        battery_pct=90,
        electrolyte_c=50.0,
        faults=("leak_small", "fan_failure"),
    ),
    Scenario(
        "cooling_failure",
        "safety",
        "Cooling pump failure",
        "Panne de la pompe de refroidissement",
        "The cooling loop stops moving water. The electrolyte climbs to 60 °C, the stack stops to cool, "
        "conditions its electrolyte and starts again: the PFA's original cycle, now a fallback.",
        "La boucle de refroidissement ne fait plus circuler l'eau. L'électrolyte monte à 60 °C, "
        "l'empilement s'arrête pour refroidir, conditionne l'électrolyte et repart : le cycle d'origine "
        "du PFA, devenu un mode de secours.",
        7,
        10,
        9.0,
        speed=300,
        battery_pct=90,
        electrolyte_c=50.0,
        faults=("cooling_failure",),
    ),
    Scenario(
        "sensor_drift",
        "trust",
        "An ageing H₂ sensor",
        "Un capteur H₂ vieillissant",
        "The detector's clean-air reading creeps up as it ages. Nothing is leaking, but the trust "
        "layer notices the drift, says so, and withdraws the production permit until a calibration.",
        "La mesure à l'air propre du détecteur monte avec l'âge. Rien ne fuit, mais la couche de "
        "confiance repère la dérive, le signale et retire l'autorisation de produire jusqu'à un étalonnage.",
        7,
        10,
        9.0,
        speed=600,
        battery_pct=90,
        electrolyte_c=50.0,
        faults=("h2_drift",),
    ),
    Scenario(
        "poisoned_sensor",
        "trust",
        "A poisoned H₂ sensor",
        "Un capteur H₂ empoisonné",
        "The detector looks perfect in clean air but has lost its sensitivity. Only a bump test with "
        "calibration gas can reveal it: run one from the maintenance panel.",
        "Le détecteur semble parfait à l'air propre mais a perdu sa sensibilité. Seul un test au gaz "
        "étalon peut le révéler : lancez-en un depuis le panneau de maintenance.",
        7,
        10,
        10.0,
        speed=60,
        battery_pct=90,
        electrolyte_c=50.0,
        faults=("h2_poisoned",),
    ),
    Scenario(
        "dropout",
        "trust",
        "The controller goes silent",
        "Le contrôleur ne répond plus",
        "The controller stops reporting. The gateway notices the silence within seconds; the "
        "controller, without a fresh permit, stops production on its own.",
        "Le contrôleur cesse d'émettre. La passerelle remarque le silence en quelques secondes ; le "
        "contrôleur, sans autorisation renouvelée, arrête la production de lui-même.",
        7,
        10,
        11.0,
        speed=20,
        battery_pct=90,
        electrolyte_c=50.0,
        faults=("dropout",),
    ),
)

BY_ID = {s.id: s for s in SCENARIOS}
