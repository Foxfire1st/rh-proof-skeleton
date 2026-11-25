-- Synthetic fixture with intentional tokens on known lines.

theorem thm1 : True := by
  sorry

theorem thm2 : True := by
  admit

axiom declP : Prop

opaque declN : Nat

unsafe def declU : Nat := 0

set_option debug.skipKernelTC true

theorem thm3 : 1 = 1 := by
  native_decide

def refReduce := ofReduceBool

def refTrust := trustCompiler

@[extern "c_sym"]
def extFn1 : Nat := 0

@[inline, extern "c_other"]
def extFn2 : Nat := 0

def fastImpl : Nat := 0

@[implemented_by fastImpl]
def slowImpl : Nat := 0

def foo : Nat := 0
attribute [extern "c_attr"] foo

def bar : Nat := 0
def barImpl : Nat := 0
local attribute [implemented_by barImpl] bar

theorem thm4 : True := by
  /- outer /- inner -/ -/ sorry
