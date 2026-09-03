; ModuleID = 'closure_k10.ll'
source_filename = "closure_k10.c"
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
  br i1 %dc_4, label %p_4, label %d_5

d_5:
  %dc_5 = icmp eq i32 %cond, 5
  br i1 %dc_5, label %p_5, label %d_6

d_6:
  %dc_6 = icmp eq i32 %cond, 6
  br i1 %dc_6, label %p_6, label %d_7

d_7:
  %dc_7 = icmp eq i32 %cond, 7
  br i1 %dc_7, label %p_7, label %d_8

d_8:
  %dc_8 = icmp eq i32 %cond, 8
  br i1 %dc_8, label %p_8, label %d_9

d_9:
  %dc_9 = icmp eq i32 %cond, 9
  br i1 %dc_9, label %p_9, label %x_0

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

p_5:
  %pc_5 = icmp sgt i32 %cond, 5
  br i1 %pc_5, label %x_0, label %y_0

p_6:
  %pc_6 = icmp sgt i32 %cond, 6
  br i1 %pc_6, label %x_0, label %y_0

p_7:
  %pc_7 = icmp sgt i32 %cond, 7
  br i1 %pc_7, label %x_0, label %y_0

p_8:
  %pc_8 = icmp sgt i32 %cond, 8
  br i1 %pc_8, label %x_0, label %y_0

p_9:
  %pc_9 = icmp sgt i32 %cond, 9
  br i1 %pc_9, label %x_0, label %y_0

x_0:
  br label %x_1

x_1:
  br label %x_2

x_2:
  br label %x_3

x_3:
  br label %x_4

x_4:
  br label %x_5

x_5:
  br label %x_6

x_6:
  br label %x_7

x_7:
  br label %x_8

x_8:
  br label %x_9

x_9:
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
  br label %y_5

y_5:
  br label %y_6

y_6:
  br label %y_7

y_7:
  br label %y_8

y_8:
  br label %y_9

y_9:
  br label %x_0

}
