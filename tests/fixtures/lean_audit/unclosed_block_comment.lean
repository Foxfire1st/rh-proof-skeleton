-- Synthetic fixture with an unclosed nested block comment.

def beforeComment : Nat := 1

/- outer /- inner -/
def hiddenByUnclosedComment : Nat := 2
