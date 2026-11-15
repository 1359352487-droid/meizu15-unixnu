// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <AsmMacroLib.h>

ASM_FUNC(DarwinCurrentEl)
  mrs x0, CurrentEL
  ret

// x0=PA, x1=size: write back and drop the data cache lines of a mapped range.
ASM_FUNC(DarwinFlush)
  cbz x1, 2f
  add x1, x0, x1
  mrs x2, ctr_el0
  ubfx x2, x2, #16, #4
  mov x3, #4
  lsl x2, x3, x2
  sub x3, x2, #1
  bic x0, x0, x3
1:
  dc civac, x0
  add x0, x0, x2
  cmp x0, x1
  b.lo 1b
  dsb sy
2:
  ret

// x0=boot_args PA, x1=entry PA, x2=owned PA, x3=owned size, x4=frame buffer
// PA (0 for none), x5=its size in bytes. EL1 only.
//
// Two painted bands, because one could not answer the question that matters.
//
// Before this, the top of the screen turned green and then nothing else
// happened. Green only proves the paint ran; the branch to the kernel comes
// after the MMU is switched off, and if instruction fetch fails at that point
// the panel looks exactly the same as a kernel that started and stayed quiet.
//
// So the branch now goes through a trampoline that paints a second band midway
// down the screen, and the three possible outcomes are all distinguishable:
//
//   green, no red   stopped between the two paints -- cache maintenance or the
//                   SCTLR_EL1 write, before any branch
//   green and red   the trampoline executed with the MMU off, so
//                   instruction fetch still works; the kernel was entered and
//                   produced no visible output of its own
//   neither         DarwinEnter was never called
//
// Colour note: these are BGRA words, so 0xFF00FF00 is green and 0xFFFF0000 is
// red -- the low byte is blue.
ASM_FUNC(DarwinEnter)
  msr daifset, #15

  // Everything the trampoline needs has to survive in registers. Once the MMU is
  // off only the identity-mapped kernel image and this code are reachable, and
  // the stack is not something to rely on for an argument like this.
  mov x15, x1        // real kernel entry PA
  mov x14, x4        // framebuffer PA, 0 when there is none
  mov x13, x5        // framebuffer size in bytes

  // Record what it would take to resume the C caller, so the return stub can
  // hand control back after a fault instead of only painting a band.
  //
  // XNU's loader-return channel gives the stub a log pointer and length and
  // nothing else; there is no way to print from there, since the 5x12 renderer
  // lives in C and needs the boot-services framebuffer lookup to find the
  // display. Saving the caller's stack pointer and return address plus the
  // translation registers means the stub can restore them and simply return,
  // and DiagDrawRaw() does the drawing in C where the font already exists.
  adr x9, DarwinSavedContext
  mov x10, sp
  stp x10, x30, [x9]          // SP, return address
  mrs x10, sctlr_el1
  mrs x11, ttbr0_el1
  mrs x12, ttbr1_el1
  mrs x13, tcr_el1
  mrs x14, mair_el1
  stp x10, x11, [x9, #16]
  stp x12, x13, [x9, #32]
  str x14, [x9, #48]
  // The callee-saved set: this routine never returns, so nothing else would
  // preserve them, and the C caller reads them the moment the stub resumes it.
  stp x19, x20, [x9, #56]
  stp x21, x22, [x9, #72]
  stp x23, x24, [x9, #88]
  stp x25, x26, [x9, #104]
  stp x27, x28, [x9, #120]
  str x29, [x9, #136]
  dc civac, x9
  dsb sy

  // Restore the registers this routine needs from the arguments again: x13 and
  // x14 were borrowed above.
  mov x14, x4        // framebuffer PA
  mov x13, x5        // framebuffer size

  // Paint the top 1/16 of the framebuffer green (BGRA 0xFF00FF00).
  cbz x4, 2f
  lsr x9, x5, #4
  bic x9, x9, #7
  mov w10, #0xFF00
  movk w10, #0xFF00, lsl #16
  mov x11, x4
3:
  stp w10, w10, [x11], #8
  subs x9, x9, #8
  b.hi 3b
  dsb sy
2:
  // Write the kernel image back out of the data cache so the kernel reads what
  // was actually loaded, then drop the instruction cache.
  add x5, x2, x3
  mrs x6, ctr_el0
  ubfx x6, x6, #16, #4
  mov x7, #4
  lsl x6, x7, x6
  sub x7, x6, #1
  bic x4, x2, x7
  dsb sy
1:
  dc civac, x4
  add x4, x4, x6
  cmp x4, x5
  b.lo 1b
  dsb sy
  ic iallu
  dsb sy
  isb

  // Hand the kernel our exception vectors before it runs.
  //
  // Everything this loader can observe ends at br. If the kernel faults on its
  // own first instruction, nothing is written anywhere and the panel looks
  // exactly like a kernel that is running quietly -- the blue band proved the
  // entry address is right, but not that the code there survives.
  //
  // VBAR_EL1 is writable at EL1 and the kernel does not overwrite it until it
  // gets far enough to install its own vectors. Catching a fault before that
  // point is therefore possible from here, and a fault that lands in our handler
  // means the kernel never reached its own vector setup. The handler paints the
  // exception class over the green band; see DarwinTrap.
  //
  // The table address goes in now; the framebuffer address is stored further
  // down, after the MMU is off. Two reasons: this driver's .text is mapped
  // read-only by the firmware's own SetUefiImageMemoryAttributes, so a store
  // into it while translation is on would take a permission fault, and with
  // translation off there is no check at all.
  adr x9, DarwinVectors
  msr vbar_el1, x9
  isb

  mrs x4, sctlr_el1
  bic x4, x4, #1
  bic x4, x4, #4
  bic x4, x4, #0x1000
  msr sctlr_el1, x4
  isb
  tlbi vmalle1
  dsb sy
  isb

  // Translation is off, so this store into our own .text no longer has to pass
  // a read-only mapping. The handler cannot keep the framebuffer in a register
  // because it has no idea what the kernel did to x0..x30 before it faulted.
  adr x10, DarwinTrapFramebuffer
  str x14, [x10]
  dc civac, x10
  dsb sy

  // Branch to the trampoline by PC-relative address, so the value is correct
  // whether or not the MMU is still translating this code.
  adr x1, 8f
  br x1

8:
  // Reaching here means the branch executed with translation off.
  cbz x14, 9f
  mov w10, #0x0000
  movk w10, #0xFFFF, lsl #16   // BGRA red
  add x12, x14, x13, lsr #1    // middle of the framebuffer
  lsr x9, x13, #5
  bic x9, x9, #7
1:
  stp w10, w10, [x12], #8
  subs x9, x9, #8
  b.hi 1b
  dsb sy

  // Identify what x15 actually points at.
  //
  // The expected word is 0x14000AED, read out of kernel.macho rather than off
  // the panel. Offset 0x45d4a8 -- which is LC_UNIXTHREAD's pc 0xfffffff0074614a8
  // minus __TEXT's vmaddr 0xfffffff007004000, and also __TEXT_EXEC file offset
  // 0x28c000 plus the 0x1d14a8 into that segment -- holds
  //
  //   ed 0a 00 14   ->   0x14000AED   (B over 0x2BB4)
  //
  // The first attempt at this compared against 0x140000ED, because that is what
  // the panel's "entry words" line appears to say. It is not: A and 0 are nearly
  // identical in this font, the panel was showing 14000AED, and the check
  // therefore failed on a correct address and painted yellow. Numbers read off a
  // photograph of a 1080-wide text console are not evidence.
  //
  // A third band, below the red one, carries the answer:
  //
  //   blue    x15 holds 0x14000AED, so it points at the kernel entry
  //   yellow  something else entirely, so the address arithmetic is wrong
  //
  // BGRA again: 0xFF0000FF is blue, 0xFFFFFF00 is yellow.
  ldr w9, [x15]
  mov w10, #0x0AED
  movk w10, #0x1400, lsl #16
  cmp w9, w10
  mov w10, #0x00FF
  movk w10, #0xFF00, lsl #16   // blue: entry word matches
  b.eq 5f
  mov w10, #0xFF00
  movk w10, #0xFFFF, lsl #16   // yellow: entry word differs
5:
  add x12, x14, x13, lsr #1
  add x12, x12, x13, lsr #3   // an eighth of the screen below the red band
  lsr x9, x13, #5
  bic x9, x9, #7
1:
  stp w10, w10, [x12], #8
  subs x9, x9, #8
  b.hi 1b
  dsb sy

9:
  br x15

// Return stub for hakios-xnu's loader-return channel.
//
// hakios patches start.s with a slot the loader is meant to fill:
//
//     Lloader_stub_slot:
//         .ascii "XNULDRST"    magic the loader searches for
//         .quad  0             PA of the loader's return stub
//         .quad  0             checkpoint number to stop at (0 = none)
//         .quad  0             (physical - virtual) offset of the image
//
// Two places read word 1 and branch to it when it is non-zero: the early
// exception vector in start.s, and loader_return() in loader_return.c. When it
// is zero the exception path falls into `cbz x9, .` -- a branch to itself.
//
// That is what this port has been looking at for the last several boots. The
// kernel was not hanging silently for a mysterious reason: it faulted somewhere
// before it could print, found no loader stub to hand the news to, and parked.
//
// DarwinEnter copies this routine to the end of the loaded image and writes the
// slot, so a fault brings control back here with registers as loader_return.c
// leaves them:
//
//     x0 = log physical address     x1 = log length
//     x2 = reason (0 = orderly end, 2 = checkpoint)
//     x3 = code
//
// The early exception path sets none of those, so nothing here may depend on
// them. Only PC-relative references are used, because this body is memcpy'd and
// must stay correct at its new address.
  .globl DarwinKernelReturnStubStart
  .globl DarwinKernelReturnStubEnd

  .balign 16
DarwinKernelReturnStubStart:
  msr daifset, #0xf

  // loader_return() leaves translation on, with TTBR0 pointing at a table that
  // maps only the 2 MiB block this stub sits in. Turning it off makes the
  // framebuffer addressable without caring which path arrived here.
  mrs x9, sctlr_el1
  bic x9, x9, #1
  msr sctlr_el1, x9
  isb

  adr x10, DarwinKernelReturnStubParameters
  ldur x10, [x10]             // framebuffer PA; ldur tolerates the address
                              // alignment the linker happens to give the
                              // parameter word
  cbz x10, 2f

  mov w12, #0x00FF
  movk w12, #0xFFFF, lsl #16  // magenta: the stub ran
  cmp x2, #2
  b.ne 1f
  mov w12, #0xFFFF
  movk w12, #0x00FF, lsl #16  // cyan: a checkpoint fired, not a fault
1:
  // 64 KiB at the framebuffer base, over whatever the trampoline painted.
  mov x13, x10
  mov x14, #0x10000
3:
  stp w12, w12, [x13], #8
  subs x14, x14, #8
  b.hi 3b
  dsb sy

  // Record the log the kernel handed over, then resume the C caller.
  //
  // reason 1 and 2 both mean the checkpoint timer fired; 0 means the kernel
  // reached an orderly end. Magenta stays for anything else, including the
  // early-exception path, which arrives with no registers set at all.
  adr x9, DarwinKernelReturnStubParameters
  ldur x9, [x9, #8]           // DarwinSavedContext PA
  cbz x9, 2f

  str x0, [x9, #144]          // log physical address
  str x1, [x9, #152]          // log length
  str x2, [x9, #168]          // reason, kept for the status line
  //
  // x3..x7 are the only description of what the kernel was doing that survives
  // the jump here, and until now they were thrown away.
  //
  // The early-exception vector in start.s sets them like this before branching:
  // x3 = ESR_EL1, x4 = ELR_EL1, x5 = FAR_EL1, x6 = LR, x7 = SP, with x2 = 1.
  // A checkpoint sets x2 = 2 and x3 = the checkpoint number, and an orderly
  // return from loader_return.c sets x2 = 0 / x3 = code. So the meaning is
  // decided by reason, and storing all five unconditionally costs nothing.
  //
  // On a fault this is the whole diagnosis: ESR names the class, ELR the
  // instruction that faulted, FAR the address it touched. The coloured band
  // could only ever say "something happened".
  //
  str x3, [x9, #176]
  str x4, [x9, #184]
  str x5, [x9, #192]
  str x6, [x9, #200]
  str x7, [x9, #208]
  mov x16, #1
  str x16, [x9, #160]         // the kernel returned
  dc civac, x9
  dsb sy

  //
  // Draw the values here as well, before anything can go wrong with the resume.
  //
  // The panel from the last four boots showed the magenta strip and nothing
  // else, which is consistent with two very different states: the stub stored
  // the values and then failed to resume, or it never found the context word and
  // skipped the store entirely. Both leave the screen identical, and the second
  // one is still on the table because the C side passes &DarwinSavedContext
  // (a relocated link-time address) while this code reaches its own data with
  // adr (PC-relative, physical once the MMU is off). If those two disagree the
  // store lands somewhere harmless and DarwinShowKernelLog never runs.
  //
  // So the barcodes are drawn here, from the registers, with no dependence on
  // the context word or the stack. Rows are above the ones DarwinTrap uses:
  //
  //   y = 1000   x2 (reason)      16 bits
  //   y = 1060   x3 (ESR)         32 bits
  //   y = 1120   x4 (ELR)         64 bits
  //   y = 1180   x5 (FAR)         64 bits
  //   y = 1240   marker 0x5A       8 bits
  //
  // DarwinTrap draws at 1400..1580 and the trampoline at the very top, so all
  // three can appear at once without overlapping.
  //
  // Framebuffer base and stride come from the parameter words, which have just
  // been proven readable: the magenta strip itself is painted from them.
  //
  adr x9, DarwinKernelReturnStubParameters
  ldur x10, [x9, #16]         // row stride in bytes, travels with the copy
  ldur x9, [x9]               // framebuffer PA

  mov x11, x2                 // keep reason, x2 is the bit count register
  mov x12, x3
  mov x13, x4
  mov x14, x5

  // The helper must be inside the copied range: DarwinDrawBarcode sits after
  // DarwinKernelReturnStubEnd, so it is not part of what the loader memcpy's,
  // and a bl into it would branch to an address that only resolves while
  // translation is on. Lstub_barcode is defined below, before the parameter
  // words, so it travels with the copy.
  mov x0, x11
  mov x1, #1000
  mov x2, #16
  bl Lstub_barcode

  mov x0, x12
  mov x1, #1060
  mov x2, #32
  bl Lstub_barcode

  mov x0, x13
  mov x1, #1120
  mov x2, #64
  bl Lstub_barcode

  mov x0, x14
  mov x1, #1180
  mov x2, #64
  bl Lstub_barcode

  mov x0, #0x5A
  mov x1, #1240
  mov x2, #8
  bl Lstub_barcode

  dsb sy

  // Reload the context word: Lstub_barcode clobbers x9 and x10.
  adr x9, DarwinKernelReturnStubParameters
  ldur x9, [x9, #8]           // DarwinSavedContext PA
  cbz x9, 2f

  // Put the translation regime back before touching the C stack: the caller's
  // frame lives at a virtual address that only resolves with its own tables.
  ldp x10, x11, [x9, #16]     // SCTLR_EL1, TTBR0_EL1
  ldp x12, x13, [x9, #32]     // TTBR1_EL1, TCR_EL1
  ldr x14, [x9, #48]          // MAIR_EL1
  msr ttbr0_el1, x11
  msr ttbr1_el1, x12
  msr tcr_el1, x13
  msr mair_el1, x14
  msr sctlr_el1, x10
  isb
  tlbi vmalle1
  dsb sy
  isb

  // Restore the callee-saved set last, and nothing may write to them after
  // this point. x9 stays usable: it is caller-saved.
  ldp x19, x20, [x9, #56]
  ldp x21, x22, [x9, #72]
  ldp x23, x24, [x9, #88]
  ldp x25, x26, [x9, #104]
  ldp x27, x28, [x9, #120]
  ldr x29, [x9, #136]

  ldr x10, [x9, #0]           // SP
  ldr x30, [x9, #8]           // return address into DarwinBoot
  mov sp, x10
  ret

2:
  b 2b

// Barcode painter, copied along with the stub.
//
//   x0 = value, x1 = y in pixels, x2 = bit count
//   x9 = framebuffer base, x10 = row stride in bytes
//
// Same layout DarwinDrawBarcode uses (see its comment): four-pixel start tick at
// x = 0, then one eight-pixel block per bit with a one-pixel gap, twenty rows
// tall, white for 1. Kept separate rather than shared because this one has to be
// inside the range that gets copied to the kernel window.
//
// Clobbers x3..x8, x11..x15. Preserves nothing else.
Lstub_barcode:
  mul x3, x1, x10
  add x3, x3, x9

  mov x4, #0
1:
  add x5, x3, x4, lsl #2
  mov w6, #0xFFFFFFFF
  mov x7, #20
2:
  str w6, [x5]
  add x5, x5, x10
  subs x7, x7, #1
  b.ne 2b
  add x4, x4, #1
  cmp x4, #4
  b.lo 1b

  mov x4, x2
3:
  cbz x4, 7f
  sub x4, x4, #1

  lsr x5, x0, x4
  and x5, x5, #1
  cbz x5, 3b

  sub x5, x2, #1
  sub x5, x5, x4
  mov x6, #9
  mul x5, x5, x6
  add x5, x5, #8

  mov x6, #0
4:
  add x7, x5, x6
  add x7, x3, x7, lsl #2
  mov w8, #0xFFFFFFFF
  mov x11, #20
5:
  str w8, [x7]
  add x7, x7, x10
  subs x11, x11, #1
  b.ne 5b
  add x6, x6, #1
  cmp x6, #8
  b.lo 4b

  b 3b
7:
  ret

  .balign 8
  .globl DarwinKernelReturnStubParameters
DarwinKernelReturnStubParameters:
  .quad 0                     // framebuffer PA, written after the copy
  .quad 0                     // DarwinSavedContext PA, written after the copy
  .quad 0                     // row stride in bytes, written after the copy
DarwinKernelReturnStubEnd:

// What it takes to resume the C caller after the kernel hands control back.
//
// The last three words are written by the stub itself, which is why this lives
// in .data: .text is mapped read-only while translation is on, and the first
// seven words are stored by DarwinEnter before the MMU goes off.
//
//   +0    SP                +56   x19, x20
//   +8    return address    +72   x21, x22
//   +16   SCTLR_EL1         +88   x23, x24
//   +24   TTBR0_EL1         +104  x25, x26
//   +32   TTBR1_EL1         +120  x27, x28
//   +40   TCR_EL1           +136  x29
//   +48   MAIR_EL1          +144  log physical address   (stub)
//                           +152  log length             (stub)
//                           +160  1 = the kernel returned (stub)
//                           +168  reason                 (stub)
//                           +176  x3 as the kernel left it (stub)
//                           +184  x4                     (stub)
//                           +192  x5                     (stub)
//                           +200  x6                     (stub)
//                           +208  x7                     (stub)
  .section .data
  .balign 8
  .globl DarwinSavedContext
DarwinSavedContext:
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .quad 0
  .text

// Exception vectors, installed with VBAR_EL1 by DarwinEnter just before the MMU
// is switched off.
//
// Sixteen entries of 128 bytes in the order the architecture defines. All of
// them lead to DarwinTrap: with translation off and the kernel's state unknown
// there is nothing to unwind, and the only useful act is to record what fired.
//
// 2048-byte alignment is what VBAR_EL1 requires; the low 11 bits are ignored by
// the hardware, so an unaligned table would silently point somewhere else.
  .balign 2048
DarwinVectors:
  b DarwinTrap              // 0x000 current EL, SP0, synchronous
  .balign 128
  b DarwinTrap              // 0x080 current EL, SP0, IRQ
  .balign 128
  b DarwinTrap              // 0x100 current EL, SP0, FIQ
  .balign 128
  b DarwinTrap              // 0x180 current EL, SP0, SError
  .balign 128
  b DarwinTrap              // 0x200 current EL, SPx, synchronous
  .balign 128
  b DarwinTrap              // 0x280 current EL, SPx, IRQ
  .balign 128
  b DarwinTrap              // 0x300 current EL, SPx, FIQ
  .balign 128
  b DarwinTrap              // 0x380 current EL, SPx, SError
  .balign 128
  b DarwinTrap              // 0x400 lower EL, AArch64, synchronous
  .balign 128
  b DarwinTrap              // 0x480 lower EL, AArch64, IRQ
  .balign 128
  b DarwinTrap              // 0x500 lower EL, AArch64, FIQ
  .balign 128
  b DarwinTrap              // 0x580 lower EL, AArch64, SError
  .balign 128
  b DarwinTrap              // 0x600 lower EL, AArch32, synchronous
  .balign 128
  b DarwinTrap              // 0x680 lower EL, AArch32, IRQ
  .balign 128
  b DarwinTrap              // 0x700 lower EL, AArch32, FIQ
  .balign 128
  b DarwinTrap              // 0x780 lower EL, AArch32, SError

// A fault landed here, which means the kernel did not get as far as installing
// its own vectors.
//
// This used to paint one coloured band, and that was a design error worth
// recording: the magenta strip the return stub paints is also 0x10000 bytes at
// the framebuffer base. A photograph of a magenta strip across the top therefore
// cannot say whether the kernel reached the stub or died before it -- two
// different events, one picture. Two builds were spent on that ambiguity.
//
// So this handler no longer paints a band. It draws the fault numbers as pixel
// barcodes at rows nothing else writes:
//
//   y = 1400   ESR_EL1   32 bits
//   y = 1460   ELR_EL1   64 bits
//   y = 1520   FAR_EL1   64 bits
//   y = 1580   marker    8 bits, always 0xA5 if the handler got this far
//
// Each bit is eight pixels wide and twenty tall, white for 1 over the untouched
// black framebuffer, followed by a one-pixel gap so adjacent ones stay
// countable, with a four-pixel tick at x = 0 marking the start of the row.
// Reading a row off a photo is mechanical, and this is the one channel that
// still works with the MMU off and boot services gone.
//
// No stack is used: everything is inline, because at this point SP belongs to
// whatever the kernel was doing.
DarwinTrap:
  msr daifset, #0xf

  adr x9, DarwinTrapFramebuffer
  ldr x9, [x9]
  cbz x9, 9f

  adr x10, DarwinTrapStride
  ldr x10, [x10]
  cbz x10, 9f

  // x0 = value, x1 = y, x2 = bit count.
  mrs x0, esr_el1
  mov x1, #1400
  mov x2, #32
  bl DarwinDrawBarcode

  mrs x0, elr_el1
  mov x1, #1460
  mov x2, #64
  bl DarwinDrawBarcode

  mrs x0, far_el1
  mov x1, #1520
  mov x2, #64
  bl DarwinDrawBarcode

  mov x0, #0xA5
  mov x1, #1580
  mov x2, #8
  bl DarwinDrawBarcode

8:
  dsb sy
9:
  b 9b

// Draw one value as a barcode.
//
//   x0 = value, x1 = y in pixels, x2 = bit count
//   x9 = framebuffer base (preserved), x10 = row stride in bytes (preserved)
//
// Clobbers x3..x8 and x11..x17. Keeps x9 and x10 so the caller can chain several
// rows without reloading them.
DarwinDrawBarcode:
  // x3 = base of this row = fb + y * stride.
  mul x3, x1, x10
  add x3, x3, x9

  // Start tick: four pixels wide, twenty rows tall, at pixel x = 0.
  mov x4, #0
1:
  add x5, x3, x4, lsl #2
  mov w6, #0xFFFFFFFF
  mov x7, #20
2:
  str w6, [x5]
  add x5, x5, x10
  subs x7, x7, #1
  b.ne 2b
  add x4, x4, #1
  cmp x4, #4
  b.lo 1b

  // Bits, most significant first.
  mov x4, x2                  // bit index countdown
3:
  cbz x4, 7f
  sub x4, x4, #1

  lsr x5, x0, x4
  and x5, x5, #1
  cbz x5, 3b                  // a zero stays background

  // Pixel x of this bit: 8 + (bitcount - 1 - index) * 9.
  //
  // Nine, not eight: one pixel of gap after every bit keeps two adjacent ones
  // countable in a photograph. Reading a solid white run is otherwise a guess
  // about where one bit ended and the next began.
  sub x5, x2, #1
  sub x5, x5, x4
  mov x6, #9
  mul x5, x5, x6
  add x5, x5, #8

  mov x6, #0
4:
  add x7, x5, x6
  add x7, x3, x7, lsl #2
  mov w8, #0xFFFFFFFF
  mov x11, #20
5:
  str w8, [x7]
  add x7, x7, x10
  subs x11, x11, #1
  b.ne 5b
  add x6, x6, #1
  cmp x6, #8
  b.lo 4b

  b 3b
7:
  ret

// Both words the trap handler reads live in .data, not .text.
//
// .text is mapped read-only while translation is on, and DarwinTrapStride is
// written from C (from Video[2]) before DarwinEnter runs -- a store into .text at
// that moment would take a permission fault and abort the boot in the one place
// that cannot report it. DarwinTrapFramebuffer is only ever written with
// translation off, so it could stay in .text, but keeping the pair together
// makes the rule easy to remember.
  .section .data
  .balign 8
  .globl DarwinTrapFramebuffer
DarwinTrapFramebuffer:
  .quad 0
  .globl DarwinTrapStride
DarwinTrapStride:
  .quad 4320
  .text


