#pragma once
#include <stdint.h>

#include "hestia_controller.h"

// Start the sensor buses and the first temperature conversion.
void sensors_begin();

// Read every sensor. Never blocks for long: the DS18B20 conversion started
// in the previous cycle is collected, and the next one is started.
hestia::Reading sensors_read(uint32_t now_ms);
