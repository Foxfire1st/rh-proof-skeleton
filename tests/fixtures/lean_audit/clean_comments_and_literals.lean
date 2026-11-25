/-! native_decide @[extern "c_fn"] -/

-- sorry admit axiom unsafe
/- outer /- sorry -/ still outer axiom -/

/-- opaque trustCompiler -/
def cleanFixture : String :=
  let s1 := "sorry \"admit\" \\" ++ "axiom"
  let s2 := r#"unsafe @[implemented_by f] "sorry""#
  let s3 := r##"nested "# sorry"##
  let c1 : Char := '"'
  let c2 : Char := '\''
  let c3 : Char := '\\'
  let c4 : Char := 'a'
  let x' := 1
  let y' := 2
  let my_sorry := x' + y'
  let sorryAx := my_sorry + 1
  let axiom_helper := sorryAx + 1
  let unsafeMode := axiom_helper + 1
  let admit_flag := unsafeMode + 1
  let _ := (c1, c2, c3, c4, admit_flag)
  s1 ++ s2 ++ s3
