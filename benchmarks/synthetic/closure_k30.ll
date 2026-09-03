; ModuleID = 'closure_k30.ll'
source_filename = "closure_k30.c"
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
  br i1 %dc_9, label %p_9, label %d_10

d_10:
  %dc_10 = icmp eq i32 %cond, 10
  br i1 %dc_10, label %p_10, label %d_11

d_11:
  %dc_11 = icmp eq i32 %cond, 11
  br i1 %dc_11, label %p_11, label %d_12

d_12:
  %dc_12 = icmp eq i32 %cond, 12
  br i1 %dc_12, label %p_12, label %d_13

d_13:
  %dc_13 = icmp eq i32 %cond, 13
  br i1 %dc_13, label %p_13, label %d_14

d_14:
  %dc_14 = icmp eq i32 %cond, 14
  br i1 %dc_14, label %p_14, label %d_15

d_15:
  %dc_15 = icmp eq i32 %cond, 15
  br i1 %dc_15, label %p_15, label %d_16

d_16:
  %dc_16 = icmp eq i32 %cond, 16
  br i1 %dc_16, label %p_16, label %d_17

d_17:
  %dc_17 = icmp eq i32 %cond, 17
  br i1 %dc_17, label %p_17, label %d_18

d_18:
  %dc_18 = icmp eq i32 %cond, 18
  br i1 %dc_18, label %p_18, label %d_19

d_19:
  %dc_19 = icmp eq i32 %cond, 19
  br i1 %dc_19, label %p_19, label %d_20

d_20:
  %dc_20 = icmp eq i32 %cond, 20
  br i1 %dc_20, label %p_20, label %d_21

d_21:
  %dc_21 = icmp eq i32 %cond, 21
  br i1 %dc_21, label %p_21, label %d_22

d_22:
  %dc_22 = icmp eq i32 %cond, 22
  br i1 %dc_22, label %p_22, label %d_23

d_23:
  %dc_23 = icmp eq i32 %cond, 23
  br i1 %dc_23, label %p_23, label %d_24

d_24:
  %dc_24 = icmp eq i32 %cond, 24
  br i1 %dc_24, label %p_24, label %d_25

d_25:
  %dc_25 = icmp eq i32 %cond, 25
  br i1 %dc_25, label %p_25, label %d_26

d_26:
  %dc_26 = icmp eq i32 %cond, 26
  br i1 %dc_26, label %p_26, label %d_27

d_27:
  %dc_27 = icmp eq i32 %cond, 27
  br i1 %dc_27, label %p_27, label %d_28

d_28:
  %dc_28 = icmp eq i32 %cond, 28
  br i1 %dc_28, label %p_28, label %d_29

d_29:
  %dc_29 = icmp eq i32 %cond, 29
  br i1 %dc_29, label %p_29, label %x_0

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

p_10:
  %pc_10 = icmp sgt i32 %cond, 10
  br i1 %pc_10, label %x_0, label %y_0

p_11:
  %pc_11 = icmp sgt i32 %cond, 11
  br i1 %pc_11, label %x_0, label %y_0

p_12:
  %pc_12 = icmp sgt i32 %cond, 12
  br i1 %pc_12, label %x_0, label %y_0

p_13:
  %pc_13 = icmp sgt i32 %cond, 13
  br i1 %pc_13, label %x_0, label %y_0

p_14:
  %pc_14 = icmp sgt i32 %cond, 14
  br i1 %pc_14, label %x_0, label %y_0

p_15:
  %pc_15 = icmp sgt i32 %cond, 15
  br i1 %pc_15, label %x_0, label %y_0

p_16:
  %pc_16 = icmp sgt i32 %cond, 16
  br i1 %pc_16, label %x_0, label %y_0

p_17:
  %pc_17 = icmp sgt i32 %cond, 17
  br i1 %pc_17, label %x_0, label %y_0

p_18:
  %pc_18 = icmp sgt i32 %cond, 18
  br i1 %pc_18, label %x_0, label %y_0

p_19:
  %pc_19 = icmp sgt i32 %cond, 19
  br i1 %pc_19, label %x_0, label %y_0

p_20:
  %pc_20 = icmp sgt i32 %cond, 20
  br i1 %pc_20, label %x_0, label %y_0

p_21:
  %pc_21 = icmp sgt i32 %cond, 21
  br i1 %pc_21, label %x_0, label %y_0

p_22:
  %pc_22 = icmp sgt i32 %cond, 22
  br i1 %pc_22, label %x_0, label %y_0

p_23:
  %pc_23 = icmp sgt i32 %cond, 23
  br i1 %pc_23, label %x_0, label %y_0

p_24:
  %pc_24 = icmp sgt i32 %cond, 24
  br i1 %pc_24, label %x_0, label %y_0

p_25:
  %pc_25 = icmp sgt i32 %cond, 25
  br i1 %pc_25, label %x_0, label %y_0

p_26:
  %pc_26 = icmp sgt i32 %cond, 26
  br i1 %pc_26, label %x_0, label %y_0

p_27:
  %pc_27 = icmp sgt i32 %cond, 27
  br i1 %pc_27, label %x_0, label %y_0

p_28:
  %pc_28 = icmp sgt i32 %cond, 28
  br i1 %pc_28, label %x_0, label %y_0

p_29:
  %pc_29 = icmp sgt i32 %cond, 29
  br i1 %pc_29, label %x_0, label %y_0

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
  br label %x_10

x_10:
  br label %x_11

x_11:
  br label %x_12

x_12:
  br label %x_13

x_13:
  br label %x_14

x_14:
  br label %x_15

x_15:
  br label %x_16

x_16:
  br label %x_17

x_17:
  br label %x_18

x_18:
  br label %x_19

x_19:
  br label %x_20

x_20:
  br label %x_21

x_21:
  br label %x_22

x_22:
  br label %x_23

x_23:
  br label %x_24

x_24:
  br label %x_25

x_25:
  br label %x_26

x_26:
  br label %x_27

x_27:
  br label %x_28

x_28:
  br label %x_29

x_29:
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
  br label %y_10

y_10:
  br label %y_11

y_11:
  br label %y_12

y_12:
  br label %y_13

y_13:
  br label %y_14

y_14:
  br label %y_15

y_15:
  br label %y_16

y_16:
  br label %y_17

y_17:
  br label %y_18

y_18:
  br label %y_19

y_19:
  br label %y_20

y_20:
  br label %y_21

y_21:
  br label %y_22

y_22:
  br label %y_23

y_23:
  br label %y_24

y_24:
  br label %y_25

y_25:
  br label %y_26

y_26:
  br label %y_27

y_27:
  br label %y_28

y_28:
  br label %y_29

y_29:
  br label %x_0

}
