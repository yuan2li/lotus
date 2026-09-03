; ModuleID = 'closure_k5.ll'
source_filename = "closure_k5.c"
target triple = "arm64-apple-macosx"

; Well-formed Proposition 5.1 family: the dispatcher chain makes every
; decision reachable from the entry, so no block is dead and the rooted
; strong-closure hypothesis (reachable start) holds.
define void @closure_func(i32 %cond) {
d_0:
  %dc_0 = icmp eq i32 %cond, 0
  br i1 %dc_0, label %p_0, label %d_1

d_1:
  %dc_1 = icmp eq i32 %cond, 1
  br i1 %dc_1, label %p_1, label %d_2

d_2:
  %dc_2 = icmp eq i32 %cond, 2
  br i1 %dc_2, label %p_2, label %d_3

d_3:
  %dc_3 = icmp eq i32 %cond, 3
  br i1 %dc_3, label %p_3, label %d_4

d_4:
  %dc_4 = icmp eq i32 %cond, 4
  br i1 %dc_4, label %p_4, label %x_0

p_0:
  %pc_0 = icmp sgt i32 %cond, 0
  br i1 %pc_0, label %x_0, label %y_0

p_1:
  %pc_1 = icmp sgt i32 %cond, 1
  br i1 %pc_1, label %x_0, label %y_0

p_2:
  %pc_2 = icmp sgt i32 %cond, 2
  br i1 %pc_2, label %x_0, label %y_0

p_3:
  %pc_3 = icmp sgt i32 %cond, 3
  br i1 %pc_3, label %x_0, label %y_0

p_4:
  %pc_4 = icmp sgt i32 %cond, 4
  br i1 %pc_4, label %x_0, label %y_0

x_0:
  br label %x_1

x_1:
  br label %x_2

x_2:
  br label %x_3

x_3:
  br label %x_4

x_4:
  br label %y_0

y_0:
  br label %y_1

y_1:
  br label %y_2

y_2:
  br label %y_3

y_3:
  br label %y_4

y_4:
  br label %x_0

}
