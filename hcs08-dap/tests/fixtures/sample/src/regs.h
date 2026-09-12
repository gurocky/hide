/* A single memory-mapped register, declared the way MCU headers do it. */
#ifndef REGS_H
#define REGS_H

typedef unsigned char byte;

typedef union {
    byte Byte;
    struct {
        byte PTAD0 :1;
        byte PTAD1 :1;
        byte PTAD2 :1;
        byte PTAD3 :1;
        byte PTAD4 :1;
        byte PTAD5 :1;
        byte PTAD6 :1;
        byte PTAD7 :1;
    } Bits;
} PTADSTR;

extern volatile __data PTADSTR __at(0x0000) _PTAD;   /* Port A data register */
#define PTAD _PTAD.Byte

#endif
