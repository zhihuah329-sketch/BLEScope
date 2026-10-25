#ifndef DEBUG_LINK_H
#define DEBUG_LINK_H

#include <stdint.h>

typedef struct
{
    const char *name;
    volatile float *value;
    float minimum;
    float maximum;
} DebugLinkParam;

/* send_byte runs in the main loop. can_write must return 1 to permit a write. */
void DebugLink_Init(const DebugLinkParam *params, uint8_t count,
                    void (*send_byte)(uint8_t), uint8_t (*can_write)(void));
/* Call from USART1 RX interrupt; no printf or parsing runs in the interrupt. */
void DebugLink_OnByte(uint8_t byte);
/* Call often from the main loop. Only one module should consume USART1 RX. */
void DebugLink_Task(void);

#endif
