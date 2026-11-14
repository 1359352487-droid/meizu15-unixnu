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
  mrs x4, sctlr_el1
  bic x4, x4, #1
  bic x4, x4, #4
  bic x4, x4, #0x1000
  msr sctlr_el1, x4
  isb
  tlbi vmalle1
  dsb sy
  isb

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

9:
  br x15
