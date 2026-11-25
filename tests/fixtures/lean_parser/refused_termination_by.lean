module

def countdown (n : Nat) : Nat :=
  match n with
  | 0 => 0
  | k + 1 => countdown k
termination_by n
