#include "stm32f10x.h"
#include "DebugLink.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

static const DebugLinkParam *Registry;
static uint8_t RegistryCount;
static void (*SendByte)(uint8_t);
static uint8_t (*CanWrite)(void);
static volatile uint8_t Rx[256];
static volatile uint16_t Head, Tail;
static volatile uint8_t Overflow;
static char Frame[96];
static uint8_t Length, Receiving;

void DebugLink_Init(const DebugLinkParam *params, uint8_t count,
                    void (*send_byte)(uint8_t), uint8_t (*can_write)(void))
{
    Registry = params;
    RegistryCount = count;
    SendByte = send_byte;
    CanWrite = can_write;
    Head = Tail = 0;
    Overflow = Length = Receiving = 0;
}

void DebugLink_OnByte(uint8_t byte)
{
    uint16_t next = (Head + 1U) & 255U;
    if (next == Tail)
    {
        Overflow = 1;
        return;
    }
    Rx[Head] = byte;
    __DMB();
    Head = next;
}

static void Reply(const char *text)
{
    if (!SendByte) return;
    while (*text) SendByte((uint8_t)*text++);
}

static void HandleFrame(char *text)
{
    char *parts[5];
    char *cursor;
    char *end;
    char output[96];
    uint8_t n = 1, i;
    unsigned long seq;
    double requested;
    float applied;
    uint32_t irqState;

    parts[0] = text;
    for (cursor = text; *cursor; cursor++)
    {
        if (*cursor == ',')
        {
            if (n == 5) return;
            *cursor = '\0';
            parts[n++] = cursor + 1;
        }
    }
    if (n < 2 || !parts[1][0]) return;
    for (cursor = parts[1]; *cursor; cursor++)
        if (*cursor < '0' || *cursor > '9') return;
    errno = 0;
    seq = strtoul(parts[1], &end, 10);
    if (errno || *end) return;

    if (strcmp(parts[0], "get") == 0 && n == 2)
    {
        for (i = 0; i < RegistryCount; i++)
        {
            if (!Registry[i].value) continue;
            irqState = __get_PRIMASK();
            __disable_irq();
            applied = *Registry[i].value;
            __set_PRIMASK(irqState);
            snprintf(output, sizeof(output), "[param,%lu,%s,%.8g]", seq,
                     Registry[i].name, (double)applied);
            Reply(output);
        }
        return;
    }
    if (strcmp(parts[0], "set") != 0 || n != 4) return;
    for (i = 0; i < RegistryCount; i++)
        if (strcmp(parts[2], Registry[i].name) == 0) break;
    if (i == RegistryCount || !Registry[i].value)
    {
        snprintf(output, sizeof(output), "[nack,%lu,unknown_parameter]", seq);
        Reply(output);
        return;
    }
    errno = 0;
    requested = strtod(parts[3], &end);
    if (errno || end == parts[3] || *end || requested != requested ||
        requested < Registry[i].minimum || requested > Registry[i].maximum)
    {
        snprintf(output, sizeof(output), "[nack,%lu,out_of_range]", seq);
        Reply(output);
        return;
    }
    irqState = __get_PRIMASK();
    __disable_irq();
    if (!CanWrite || !CanWrite())
    {
        __set_PRIMASK(irqState);
        snprintf(output, sizeof(output), "[nack,%lu,write_locked]", seq);
        Reply(output);
        return;
    }
    *Registry[i].value = (float)requested;
    applied = *Registry[i].value;
    __set_PRIMASK(irqState);
    snprintf(output, sizeof(output), "[ack,%lu,%s,%.8g]", seq,
             Registry[i].name, (double)applied);
    Reply(output);
}

void DebugLink_Task(void)
{
    uint8_t byte;
    uint16_t budget = 256;
    uint32_t irqState;
    if (Overflow)
    {
        irqState = __get_PRIMASK();
        __disable_irq();
        Tail = Head;
        Overflow = 0;
        __set_PRIMASK(irqState);
        Receiving = Length = 0;
        return;
    }
    while (Tail != Head && budget--)
    {
        byte = Rx[Tail];
        Tail = (Tail + 1U) & 255U;
        if (byte == '[')
        {
            Length = 0;
            Receiving = 1;
        }
        else if (Receiving && byte == ']')
        {
            if (Overflow) return;
            Frame[Length] = '\0';
            Receiving = 0;
            HandleFrame(Frame);
        }
        else if (Receiving)
        {
            if (Length >= sizeof(Frame) - 1 || byte < 32 || byte > 126)
                Receiving = Length = 0;
            else
                Frame[Length++] = (char)byte;
        }
    }
}
