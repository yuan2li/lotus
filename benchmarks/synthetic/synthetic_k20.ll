; ModuleID = 'synthetic_k20.ll'
source_filename = "synthetic_k20.c"
target datalayout = "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-f80:128-n8:16:32:64-S128"
target triple = "x86_64-unknown-linux-gnu"

define void @synthetic_func(i32 %cond) {
entry:
  br label %p_0

p_0:
  %cmp_0 = icmp eq i32 %cond, 0
  br i1 %cmp_0, label %x_0, label %y_0

p_1:
  %cmp_1 = icmp eq i32 %cond, 1
  br i1 %cmp_1, label %x_0, label %y_0

p_2:
  %cmp_2 = icmp eq i32 %cond, 2
  br i1 %cmp_2, label %x_0, label %y_0

p_3:
  %cmp_3 = icmp eq i32 %cond, 3
  br i1 %cmp_3, label %x_0, label %y_0

p_4:
  %cmp_4 = icmp eq i32 %cond, 4
  br i1 %cmp_4, label %x_0, label %y_0

p_5:
  %cmp_5 = icmp eq i32 %cond, 5
  br i1 %cmp_5, label %x_0, label %y_0

p_6:
  %cmp_6 = icmp eq i32 %cond, 6
  br i1 %cmp_6, label %x_0, label %y_0

p_7:
  %cmp_7 = icmp eq i32 %cond, 7
  br i1 %cmp_7, label %x_0, label %y_0

p_8:
  %cmp_8 = icmp eq i32 %cond, 8
  br i1 %cmp_8, label %x_0, label %y_0

p_9:
  %cmp_9 = icmp eq i32 %cond, 9
  br i1 %cmp_9, label %x_0, label %y_0

p_10:
  %cmp_10 = icmp eq i32 %cond, 10
  br i1 %cmp_10, label %x_0, label %y_0

p_11:
  %cmp_11 = icmp eq i32 %cond, 11
  br i1 %cmp_11, label %x_0, label %y_0

p_12:
  %cmp_12 = icmp eq i32 %cond, 12
  br i1 %cmp_12, label %x_0, label %y_0

p_13:
  %cmp_13 = icmp eq i32 %cond, 13
  br i1 %cmp_13, label %x_0, label %y_0

p_14:
  %cmp_14 = icmp eq i32 %cond, 14
  br i1 %cmp_14, label %x_0, label %y_0

p_15:
  %cmp_15 = icmp eq i32 %cond, 15
  br i1 %cmp_15, label %x_0, label %y_0

p_16:
  %cmp_16 = icmp eq i32 %cond, 16
  br i1 %cmp_16, label %x_0, label %y_0

p_17:
  %cmp_17 = icmp eq i32 %cond, 17
  br i1 %cmp_17, label %x_0, label %y_0

p_18:
  %cmp_18 = icmp eq i32 %cond, 18
  br i1 %cmp_18, label %x_0, label %y_0

p_19:
  %cmp_19 = icmp eq i32 %cond, 19
  br i1 %cmp_19, label %x_0, label %y_0

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
  br label %x_0

  ret void
}
