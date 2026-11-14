#include "IR/UseTraceSSA/DefectDetector.h"
#include "IR/UseTraceSSA/PathFeasibility.h"
#include "IR/UseTraceSSA/SVFGBridge.h"
#include <gtest/gtest.h>
#include <llvm/AsmParser/Parser.h>
#include <llvm/IR/InstIterator.h>
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/Support/SourceMgr.h>
#include <map>
using namespace lotus::usetracessa;
namespace {

struct FeasibilityConfiguration {
  bool eventOrder;
  bool fixedObjects;
};

class UseTraceSSAPathFeasibility
    : public ::testing::TestWithParam<FeasibilityConfiguration> {};

/// Feasibility of every (finding, object) witness, keyed by the function
/// that allocates the object (one malloc %p per function, object 10 + index).
std::map<std::string, Feasibility> witnesses(const char *ir, NativeHistoryMode mode,
                                           DefectKind kind, Automaton automaton,
                                           FeasibilityConfiguration configuration) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(ir, diagnostic, context);
  EXPECT_TRUE(module);
  if (!module)
    return {};
  lotus::analysis::SVFG svfg;
  std::map<ObjectID, std::string> owners;
  ObjectID next = 10;
  for (const auto &function : *module)
    for (const auto &instruction : llvm::instructions(function))
      if (instruction.getName() == "p") {
        owners[next] = function.getName().str();
        svfg.addObjectForValue(&instruction, next++);
      }
  NativeLocations locations;
  NativeHistoryOptions options;
  options.locations = &locations;
  options.eventOrder = configuration.eventOrder;
  auto result = buildUseTraceSSAFromLotusSVFG(svfg, *module, mode, options);
  EXPECT_TRUE(result.graph.verify());
  EXPECT_EQ(result.eventOrder.has_value(), configuration.eventOrder);
  DefectScanOptions scanOptions;
  scanOptions.fixedObjects = configuration.fixedObjects;
  auto scan = DefectDetector(result.graph, 3).scan(kind, scanOptions);
  PathFeasibility checker(result.graph, locations, std::move(automaton));
  std::map<std::string, Feasibility> verdicts;
  for (std::size_t index = 0; index < scan.findings.size(); ++index)
    for (auto object : scan.findings[index].objects) {
      auto witness = scan.materialize(index, result.graph, object);
      EXPECT_TRUE(witness.result.found());
      verdicts[owners.at(object)] = checker.check(witness.result, object);
    }
  return verdicts;
}

constexpr const char *Declarations = R"IR(
  declare i8* @malloc(i64)
  declare void @free(i8*)
  declare void @use(i8)
)IR";

std::string program(const char *body) { return std::string(Declarations) + body; }

} // namespace

TEST_P(UseTraceSSAPathFeasibility, CorrelatedBranchesExcludeDoubleFree) {
  auto ir = program(R"IR(
    define void @correlated(i32 %c) {
    entry:
      %p = call i8* @malloc(i64 8)
      %t = icmp ne i32 %c, 0
      br i1 %t, label %first, label %join
    first:
      call void @free(i8* %p)
      br label %join
    join:
      %u = icmp eq i32 %c, 0
      br i1 %u, label %second, label %exit
    second:
      call void @free(i8* %p)
      br label %exit
    exit:
      ret void
    }
    define void @twice(i32 %c) {
    entry:
      %p = call i8* @malloc(i64 8)
      %t = icmp ne i32 %c, 0
      br i1 %t, label %first, label %join
    first:
      call void @free(i8* %p)
      br label %join
    join:
      call void @free(i8* %p)
      ret void
    }
  )IR");
  auto verdicts = witnesses(ir.c_str(), NativeHistoryMode::DoubleFree,
                            DefectKind::DoubleFree, Automaton::doubleFree(), GetParam());
  EXPECT_EQ(verdicts.at("correlated"), Feasibility::Infeasible);
  EXPECT_EQ(verdicts.at("twice"), Feasibility::Feasible);
}

TEST_P(UseTraceSSAPathFeasibility, CorrelatedBranchesExcludeUseAfterFree) {
  auto ir = program(R"IR(
    define void @correlated(i32 %c) {
    entry:
      %p = call i8* @malloc(i64 8)
      %t = icmp eq i32 %c, 3
      br i1 %t, label %release, label %join
    release:
      call void @free(i8* %p)
      br label %join
    join:
      %u = icmp ne i32 %c, 3
      br i1 %u, label %read, label %exit
    read:
      %v = load i8, i8* %p
      call void @use(i8 %v)
      br label %exit
    exit:
      ret void
    }
    define void @stale(i32 %c) {
    entry:
      %p = call i8* @malloc(i64 8)
      %t = icmp eq i32 %c, 3
      br i1 %t, label %release, label %join
    release:
      call void @free(i8* %p)
      br label %join
    join:
      %v = load i8, i8* %p
      call void @use(i8 %v)
      ret void
    }
  )IR");
  auto verdicts = witnesses(ir.c_str(), NativeHistoryMode::UseAfterFree,
                            DefectKind::UseAfterFree, Automaton::useAfterFree(), GetParam());
  EXPECT_EQ(verdicts.at("correlated"), Feasibility::Infeasible);
  EXPECT_EQ(verdicts.at("stale"), Feasibility::Feasible);
}

TEST_P(UseTraceSSAPathFeasibility, LeaksAvoidMustReleasesAndFailedAllocations) {
  auto ir = program(R"IR(
    define void @checked() {
    entry:
      %p = call i8* @malloc(i64 8)
      %ok = icmp ne i8* %p, null
      br i1 %ok, label %release, label %exit
    release:
      call void @free(i8* %p)
      br label %exit
    exit:
      ret void
    }
    define void @either(i32 %c) {
    entry:
      %p = call i8* @malloc(i64 8)
      %t = icmp ne i32 %c, 0
      br i1 %t, label %first, label %join
    first:
      call void @free(i8* %p)
      br label %join
    join:
      %u = icmp eq i32 %c, 0
      br i1 %u, label %second, label %exit
    second:
      call void @free(i8* %p)
      br label %exit
    exit:
      ret void
    }
    ; Leaks when c != 3. Its witness may pass the free(null) branch, which the
    ; history merges with the other one: the check must not depend on that.
    define void @leaky(i32 %c) {
    entry:
      %p = call i8* @malloc(i64 8)
      %t = icmp eq i32 %c, 3
      br i1 %t, label %release, label %join
    release:
      call void @free(i8* %p)
      br label %join
    join:
      %u = icmp ne i32 %c, 3
      br i1 %u, label %exit, label %other
    other:
      call void @free(i8* null)
      br label %exit
    exit:
      ret void
    }
  )IR");
  auto verdicts = witnesses(ir.c_str(), NativeHistoryMode::MemoryLeak,
                            DefectKind::MemoryLeak, Automaton::memoryLeak(), GetParam());
  EXPECT_EQ(verdicts.at("checked"), Feasibility::Infeasible);
  EXPECT_EQ(verdicts.at("either"), Feasibility::Infeasible);
  EXPECT_EQ(verdicts.at("leaky"), Feasibility::Feasible);
}

TEST_P(UseTraceSSAPathFeasibility, BranchesInLoopsStayUnconstrained) {
  auto ir = program(R"IR(
    define void @loop(i32 %n) {
    entry:
      %p = call i8* @malloc(i64 8)
      br label %head
    head:
      %i = phi i32 [ 0, %entry ], [ %next, %body ]
      %more = icmp ne i32 %i, 2
      br i1 %more, label %body, label %exit
    body:
      call void @free(i8* %p)
      %next = add i32 %i, 1
      br label %head
    exit:
      ret void
    }
  )IR");
  auto verdicts = witnesses(ir.c_str(), NativeHistoryMode::DoubleFree,
                            DefectKind::DoubleFree, Automaton::doubleFree(), GetParam());
  EXPECT_EQ(verdicts.at("loop"), Feasibility::Feasible);
}

INSTANTIATE_TEST_SUITE_P(
    SkeletonAndSolver, UseTraceSSAPathFeasibility,
    ::testing::Values(FeasibilityConfiguration{false, false},
                      FeasibilityConfiguration{false, true},
                      FeasibilityConfiguration{true, false},
                      FeasibilityConfiguration{true, true}),
    [](const ::testing::TestParamInfo<FeasibilityConfiguration> &info) {
      return std::string(info.param.eventOrder ? "EventOrder" : "History") +
             (info.param.fixedObjects ? "Fixed" : "Batch");
    });
