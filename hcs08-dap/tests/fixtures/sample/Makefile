# Sample S08 firmware used as the hcs08-dap test fixture.
# The committed build/ outputs are what the tests parse; rebuild only on purpose
# (addresses and line numbers asserted by the tests will change):
#   make clean all    (needs sdcc 4.x)

SDCC   ?= sdcc
TARGET := sample
BUILD  := build

# Generic MC9S08AW60-like memory map
CFLAGS  := -ms08 -Isrc --opt-code-size --debug --stack-loc 0x086F
LDFLAGS := -ms08 --code-loc 0x1860 --data-loc 0x0070 --xram-loc 0x0100 --xram-size 0x06F0 \
           --stack-loc 0x086F --debug --out-fmt-s19

# Link order fixes the address order the tests rely on: main < spi < crc8
OBJS := $(BUILD)/main.rel $(BUILD)/regs.rel $(BUILD)/spi.rel $(BUILD)/crc8.rel

all: $(BUILD)/$(TARGET).s19

$(BUILD)/%.rel: src/%.c
	$(SDCC) $(CFLAGS) -c $< -o $@

$(BUILD)/$(TARGET).s19: $(OBJS)
	$(SDCC) $(LDFLAGS) -o $@ $(OBJS)

clean:
	rm -f $(BUILD)/*

.PHONY: all clean
