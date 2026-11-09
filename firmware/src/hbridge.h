#ifndef HBRIDGE_H_
#define HBRIDGE_H_

int  hbridge_init(void);     /* starts the 50 Hz SPWM on both channels */
void hbridge_stop(void);     /* both channels to 0% duty */

#endif