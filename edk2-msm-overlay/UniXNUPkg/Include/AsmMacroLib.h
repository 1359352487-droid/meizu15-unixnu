// SPDX-License-Identifier: BSD-2-Clause-Patent
//
// Minimal stand-in for MdePkg's AsmMacroLib.h.
//
// DarwinBootPkg/Driver/Entry.S starts with "#include <AsmMacroLib.h>", but the
// edk2 revision edk2-msm pins only ships the older AsmMacroIoLib.h under
// Library/. Putting this header in UniXNUPkg/Include means it is on the include
// path for every module that depends on UniXNUPkg (DarwinBoot does), without
// patching upstream sources.
//
// AArch64 has no leading-underscore symbol decoration.
//
#ifndef ASM_MACRO_LIB_H_
#define ASM_MACRO_LIB_H_

#define ASM_PFX(name)  name

#define ASM_FUNC(Name)            \
  .text;                          \
  .p2align 2;                     \
  .global ASM_PFX(Name);          \
  .type ASM_PFX(Name), %function; \
ASM_PFX(Name):

#define ASM_GLOBAL_FUNC(Name, ...) \
  ASM_FUNC(Name)                   \
  __VA_ARGS__

#define ASM_LABEL_END \
  .ltorg

#endif
