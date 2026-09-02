; ModuleID = 'synthetic_k5.ll'
source_filename = "synthetic_k5.c"
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

  ret void
}
