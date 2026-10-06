#pragma once
#include "hestia_controller.h"

// Every output off and the emergency relay open: the state the hardware is
// left in before the controller has decided anything.
void actuators_begin();

void actuators_apply(const hestia::Outputs& out);
