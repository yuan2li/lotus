#include "IR/UseTraceSSA/DefectDetector.h"
#include "IR/UseTraceSSA/LLVMImporter.h"
#include "IR/UseTraceSSA/SVFGBridge.h"

#include <sstream>

#include <gtest/gtest.h>
#include <llvm/AsmParser/Parser.h>
#include <llvm/IR/Instructions.h>
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/Support/SourceMgr.h>

using namespace lotus::usetracessa;
namespace {
// Fixed-object scans must agree with the mask-lifted batch scan on every sink,
// object and per-object status, with eager and deferred witnesses.
void expectFixedMatchesBatch(const TraceFlowGraph &graph, DefectKind kind) {
  for (bool witnesses : {true, false}) {
    DefectScanOptions batchOptions{witnesses, true, false, true};
    DefectScanOptions fixedOptions = batchOptions;
    fixedOptions.fixedObjects = true;
    auto batch = DefectDetector(graph, 3).scan(kind, batchOptions);
    auto fixed = DefectDetector(graph, 3).scan(kind, fixedOptions);
    EXPECT_EQ(fixed.status, batch.status);
    EXPECT_EQ(fixed.exhaustive, batch.exhaustive);
    EXPECT_EQ(fixed.statistics.candidateObjects, batch.statistics.candidateObjects);
    EXPECT_EQ(fixed.statistics.foundObjects, batch.statistics.foundObjects);
    EXPECT_EQ(fixed.statistics.notFoundObjects, batch.statistics.notFoundObjects);
    EXPECT_EQ(fixed.statistics.unknownObjects, batch.statistics.unknownObjects);
    EXPECT_EQ(fixed.objectStatus, batch.objectStatus);
    EXPECT_EQ(fixed.objectStatus.size(), batch.statistics.candidateObjects);
    // Proposition 5.2 counts the batch's pushed object bits R. A fixed-object
    // search may stop once every sink is accepted, so it pushes at most R.
    EXPECT_LE(fixed.statistics.pushes, batch.statistics.pushedObjectBits);
    EXPECT_GT(fixed.statistics.pushes, 0u);
    EXPECT_EQ(fixed.statistics.pushedObjectBits, fixed.statistics.pushes);
    ASSERT_EQ(fixed.findings.size(), batch.findings.size());
    for (std::size_t i = 0; i < fixed.findings.size(); ++i) {
      EXPECT_EQ(fixed.findings[i].sink, batch.findings[i].sink);
      EXPECT_EQ(fixed.findings[i].objects, batch.findings[i].objects);
      auto witness = fixed.materialize(i, graph);
      ASSERT_TRUE(witness.result.found());
      EXPECT_EQ(witness.result.nodes.back(), fixed.findings[i].sink);
    }
  }
}

TEST(UseTraceSSANative, LabelsPreserveUnnamedValuesAndMetadata) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare void @free(i8*)
    define i8 @first(i8* %0) {
      %2 = load i8, i8* %0, !tag !0
      call void @free(i8* %0)
      ret i8 %2
    }
    define i8 @second(i8* %0) {
      %2 = getelementptr i8, i8* %0, i64 1
      %3 = load i8, i8* %2, !tag !1
      switch i8 %3, label %done [ i8 0, label %zero ]
    zero:
      ret i8 0
    done:
      ret i8 %3
    }
    !0 = !{i32 17}
    !1 = !{i32 29}
  )IR", diagnostic, context);
  ASSERT_TRUE(module);
  auto moduleHistories = LLVMHistoryBuilder::build(*module);
  ASSERT_EQ(moduleHistories.size(), 2u);
  for (const auto &history : moduleHistories) {
    auto single = LLVMHistoryBuilder::build(history.function());
    ASSERT_EQ(history.graph().program().operations().size(),
              single.graph().program().operations().size());
    for (std::size_t i = 0; i < history.graph().program().operations().size();
         ++i)
      EXPECT_EQ(history.graph().program().operations()[i].label,
                single.graph().program().operations()[i].label);
  }
  std::string moduleText;
  llvm::raw_string_ostream moduleOut(moduleText);
  module->print(moduleOut, nullptr);
  moduleOut.flush();
  lotus::analysis::SVFG svfg;
  for (auto mode : {NativeHistoryMode::Full, NativeHistoryMode::UseAfterFree}) {
    auto imported = buildUseTraceSSAFromLotusSVFG(svfg, *module, mode);
    for (const auto &function : *module) {
      auto history = LLVMHistoryBuilder::build(function);
      for (const auto &block : function) for (const auto &instruction : block) {
        const auto &expected =
            history.graph().program().operations().at(history.siteID(instruction)).label;
        // Metadata slots now consistently refer to the containing module,
        // including when the next function has a different metadata attachment.
        EXPECT_NE(moduleText.find(expected), std::string::npos) << expected;
        if (mode != NativeHistoryMode::Full && !llvm::isa<llvm::LoadInst>(instruction)) continue;
        bool present = false;
        for (const auto &node : imported.graph.nodes())
          present |= node.label.find(expected) != std::string::npos;
        EXPECT_TRUE(present) << expected;
      }
    }
  }
}
TEST(UseTraceSSANative, SharedCallsAndUnknownFacts) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto m = llvm::parseAssemblyString(R"IR(
    declare void @free(i8*)
    define void @release(i8* %p) {
      call void @free(i8* %p)
      ret void
    }
    define i8 @caller(i8* %p) {
      call void @release(i8* %p)
      %v = load i8, i8* %p
      ret i8 %v
    }
  )IR",diagnostic,context);
  ASSERT_TRUE(m);
  lotus::analysis::SVFG svfg;
  for (const char *name : {"caller","release"})
    svfg.addObjectForValue(m->getFunction(name)->getArg(0),42);
  for (auto mode : {NativeHistoryMode::Full,NativeHistoryMode::UseAfterFree}) {
    auto r=buildUseTraceSSAFromLotusSVFG(svfg,*m,mode);
    EXPECT_TRUE(r.graph.verify());
    auto q=queries::useAfterFree(r.graph); q.memoryObject=42;
    auto fixed=QueryEngine(r.graph).run(q); EXPECT_TRUE(fixed.found());
    bool returned=false;
    for (auto id : fixed.edges) returned |= r.graph.edge(id).kind==FlowKind::Return;
    EXPECT_TRUE(returned);
    q.memoryObject.reset();
    auto batch=QueryEngine(r.graph).runObjects({q,ObjectUniverse({42,99,UnknownResource})});
    EXPECT_EQ(batch.status(42),fixed.status);
    for (auto object : {ObjectID(99),UnknownResource}) {
      q.memoryObject=object;
      EXPECT_EQ(batch.status(object),QueryEngine(r.graph).run(q).status);
    }
    EXPECT_EQ(DefectDetector(r.graph).scan(DefectKind::UseAfterFree).status,QueryStatus::Found);
  }
  lotus::analysis::SVFG unknown;
  auto r=buildUseTraceSSAFromLotusSVFG(unknown,*m,NativeHistoryMode::UseAfterFree);
  EXPECT_EQ(r.graph.resourceCandidates(),std::vector<ObjectID>({UnknownResource}));
  EXPECT_EQ(DefectDetector(r.graph).run(DefectKind::UseAfterFree).result.status,QueryStatus::Found);
  for (const auto &issue : r.graph.issues())
    EXPECT_EQ(issue.find("points-to set is unknown"),std::string::npos);
}
TEST(UseTraceSSANative, PropertySelectsEventsSitesAndObjectUniverse) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare i8* @malloc(i64)
    declare void @free(i8*)
    declare i8* @fopen(i8*, i8*)
    declare i32 @fclose(i8*)
    declare void @llvm.assume(i1)
    define void @mixed(i8* %unrelated, i8* %unknown) {
      %heap = call i8* @malloc(i64 8)
      %heap_leak = call i8* @malloc(i64 8)
      call void @free(i8* %heap)
      call void @free(i8* %heap)
      %used = load i8, i8* %heap
      %irrelevant = load i8, i8* %unrelated
      %possible_use = load i8, i8* %unknown
      %file = call i8* @fopen(i8* null, i8* null)
      %file_leak = call i8* @fopen(i8* null, i8* null)
      %closed = call i32 @fclose(i8* %file)
      call void @llvm.assume(i1 true)
      ret void
    }
  )IR", diagnostic, context);
  ASSERT_TRUE(module);
  auto &function = *module->getFunction("mixed");
  lotus::analysis::SVFG svfg;
  svfg.addObjectForValue(function.getArg(0), 99);
  for (const auto &block : function) for (const auto &instruction : block) {
    if (instruction.getName() == "heap") svfg.addObjectForValue(&instruction, 10);
    if (instruction.getName() == "heap_leak") svfg.addObjectForValue(&instruction, 11);
    if (instruction.getName() == "file") svfg.addObjectForValue(&instruction, 20);
    if (instruction.getName() == "file_leak") svfg.addObjectForValue(&instruction, 21);
  }
  auto full = buildUseTraceSSAFromLotusSVFG(svfg, *module, NativeHistoryMode::Full);
  const auto heap = Event::Allocate | Event::Release;
  for (auto mode : {NativeHistoryMode::DoubleFree, NativeHistoryMode::UseAfterFree,
                    NativeHistoryMode::MemoryLeak, NativeHistoryMode::FileLeak}) {
    auto result = buildUseTraceSSAFromLotusSVFG(svfg, *module, mode);
    EXPECT_TRUE(result.graph.verify());
    EXPECT_LT(result.graph.nodes().size(), full.graph.nodes().size());
    const bool file = mode == NativeHistoryMode::FileLeak;
    EXPECT_EQ(result.graph.resourceCandidates(),
              file ? std::vector<ObjectID>({20, 21}) : std::vector<ObjectID>({10, 11}));
    Event allowed = file ? Event::Open | Event::Close | Event::Escape | Event::Exit : heap;
    if (mode == NativeHistoryMode::UseAfterFree) allowed = allowed | Event::Dereference;
    if (mode == NativeHistoryMode::MemoryLeak) allowed = allowed | Event::Escape | Event::Exit;
    bool unknownAccess = false;
    for (const auto &node : result.graph.nodes()) {
      EXPECT_EQ(node.label.find("@llvm.assume"), std::string::npos);
      EXPECT_EQ(node.label.find("%unrelated"), std::string::npos);
      EXPECT_EQ(node.label.find(file ? "@malloc" : "@fopen"), std::string::npos);
      EXPECT_EQ(static_cast<unsigned>(node.events) & ~static_cast<unsigned>(allowed), 0u);
      for (const auto &effect : node.effects) {
        EXPECT_EQ(static_cast<unsigned>(effect.events) & ~static_cast<unsigned>(allowed), 0u);
        if (effect.events == Event::Dereference && effect.objects.isUnknown()) unknownAccess = true;
      }
    }
    EXPECT_EQ(unknownAccess, mode == NativeHistoryMode::UseAfterFree);
    auto kind = mode == NativeHistoryMode::DoubleFree ? DefectKind::DoubleFree :
                mode == NativeHistoryMode::UseAfterFree ? DefectKind::UseAfterFree :
                mode == NativeHistoryMode::MemoryLeak ? DefectKind::MemoryLeak : DefectKind::FileLeak;
    EXPECT_EQ(DefectDetector(result.graph, 3).scan(kind).status, QueryStatus::Found);
    expectFixedMatchesBatch(result.graph, kind);
    if (mode == NativeHistoryMode::DoubleFree || mode == NativeHistoryMode::UseAfterFree) {
      auto expected = DefectDetector(full.graph, 3).scan(kind);
      auto actual = DefectDetector(result.graph, 3).scan(kind);
      EXPECT_EQ(actual.findings.size(), expected.findings.size());
      for (const auto &finding : actual.findings)
        EXPECT_EQ(finding.objects, std::vector<ObjectID>({10}));
    }
  }
}

TEST(UseTraceSSANative, DenseControlFlowMatchesSparseSkeleton) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare i8* @malloc(i64)
    declare void @free(i8*)
    define void @release(i8* %p) {
      call void @free(i8* %p)
      ret void
    }
    define void @flow(i1 %c, i8* %other) {
    entry:
      %heap = call i8* @malloc(i64 8)
      %spare = call i8* @malloc(i64 8)
      %noise = load i8, i8* %other
      br i1 %c, label %left, label %empty
    left:
      call void @release(i8* %heap)
      br label %join
    empty:
      br label %join
    join:
      %used = load i8, i8* %heap
      %more = add i8 %used, %noise
      br i1 %c, label %again, label %done
    again:
      call void @free(i8* %heap)
      br label %done
    done:
      call void @free(i8* %spare)
      ret void
    }
  )IR", diagnostic, context);
  ASSERT_TRUE(module);
  auto &function = *module->getFunction("flow");
  lotus::analysis::SVFG svfg;
  svfg.addObjectForValue(function.getArg(1), 99);
  svfg.addObjectForValue(module->getFunction("release")->getArg(0), 10);
  for (const auto &block : function) for (const auto &instruction : block) {
    if (instruction.getName() == "heap") svfg.addObjectForValue(&instruction, 10);
    if (instruction.getName() == "spare") svfg.addObjectForValue(&instruction, 11);
  }
  NativeHistoryOptions denseOptions;
  denseOptions.denseControlFlow = true;
  for (auto mode : {NativeHistoryMode::DoubleFree, NativeHistoryMode::UseAfterFree,
                    NativeHistoryMode::MemoryLeak}) {
    auto sparse = buildUseTraceSSAFromLotusSVFG(svfg, *module, mode);
    auto dense = buildUseTraceSSAFromLotusSVFG(svfg, *module, mode, denseOptions);
    ASSERT_TRUE(sparse.graph.verify());
    ASSERT_TRUE(dense.graph.verify());
    EXPECT_GT(dense.graph.statistics().historyNodes, sparse.graph.statistics().historyNodes);
    EXPECT_EQ(dense.graph.resourceCandidates(), sparse.graph.resourceCandidates());
    bool noise = false, emptyBlock = false;
    for (const auto &node : dense.graph.nodes()) {
      noise |= node.label.find("%noise") != std::string::npos;
      emptyBlock |= node.label.find("br label %join") != std::string::npos;
    }
    EXPECT_TRUE(noise);
    EXPECT_TRUE(emptyBlock);
    auto kind = mode == NativeHistoryMode::DoubleFree ? DefectKind::DoubleFree :
                mode == NativeHistoryMode::UseAfterFree ? DefectKind::UseAfterFree :
                                                          DefectKind::MemoryLeak;
    DefectScanOptions options{true, true, false, true};
    auto expected = DefectDetector(sparse.graph, 3).scan(kind, options);
    auto actual = DefectDetector(dense.graph, 3).scan(kind, options);
    EXPECT_EQ(actual.status, expected.status);
    EXPECT_EQ(actual.statistics.foundObjects, expected.statistics.foundObjects);
    EXPECT_EQ(actual.statistics.notFoundObjects, expected.statistics.notFoundObjects);
    ASSERT_EQ(actual.findings.size(), expected.findings.size());
    for (std::size_t i = 0; i < actual.findings.size(); ++i) {
      EXPECT_EQ(actual.findings[i].objects, expected.findings[i].objects);
      EXPECT_EQ(dense.graph.node(actual.findings[i].sink).label.substr(
                    dense.graph.node(actual.findings[i].sink).label.find(' ')),
                sparse.graph.node(expected.findings[i].sink).label.substr(
                    sparse.graph.node(expected.findings[i].sink).label.find(' ')));
    }
    if (mode != NativeHistoryMode::MemoryLeak) EXPECT_FALSE(actual.findings.empty());
    expectFixedMatchesBatch(dense.graph, kind);
  }
}

TEST(UseTraceSSANative, ResolvedIndirectCallsJoinTheHistory) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare i8* @malloc(i64)
    declare void @free(i8*)
    define void @release(i8* %p) {
      call void @free(i8* %p)
      ret void
    }
    define void @keep(i8* %p) {
      ret void
    }
    define void @flow(void (i8*)* %callback) {
      %heap = call i8* @malloc(i64 8)
      call void %callback(i8* %heap)
      %used = load i8, i8* %heap
      ret void
    }
  )IR", diagnostic, context);
  ASSERT_TRUE(module);
  auto &function = *module->getFunction("flow");
  const llvm::CallBase *indirect = nullptr;
  lotus::analysis::SVFG svfg;
  svfg.addObjectForValue(module->getFunction("release")->getArg(0), 10);
  for (const auto &block : function) for (const auto &instruction : block) {
    if (instruction.getName() == "heap") svfg.addObjectForValue(&instruction, 10);
    if (const auto *call = llvm::dyn_cast<llvm::CallBase>(&instruction))
      if (!call->getCalledFunction()) indirect = call;
  }
  ASSERT_TRUE(indirect);
  // Unresolved: the free behind the function pointer is invisible and the
  // model is incomplete.
  auto unresolved = buildUseTraceSSAFromLotusSVFG(svfg, *module,
                                                  NativeHistoryMode::UseAfterFree);
  auto before = DefectDetector(unresolved.graph, 3).scan(DefectKind::UseAfterFree);
  EXPECT_TRUE(before.findings.empty());
  // Resolved to both targets: the path through @release reaches the load.
  svfg.addCalleeToIndCallSite(module->getFunction("release"), indirect);
  svfg.addCalleeToIndCallSite(module->getFunction("keep"), indirect);
  auto resolved = buildUseTraceSSAFromLotusSVFG(svfg, *module,
                                                NativeHistoryMode::UseAfterFree);
  ASSERT_TRUE(resolved.graph.verify());
  // External malloc/free stay incomplete; only the indirect call is resolved.
  EXPECT_EQ(resolved.graph.issues().size() + 1, unresolved.graph.issues().size());
  auto after = DefectDetector(resolved.graph, 3).scan(DefectKind::UseAfterFree);
  EXPECT_EQ(after.status, QueryStatus::Found);
  ASSERT_EQ(after.findings.size(), 1u);
  EXPECT_EQ(after.findings[0].objects, std::vector<ObjectID>({10}));
  expectFixedMatchesBatch(resolved.graph, DefectKind::UseAfterFree);
}

TEST(UseTraceSSANative, AllocationSiteGranularityMergesAbstractCopies) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare i8* @malloc(i64)
    declare void @free(i8*)
    define void @site() {
      %heap = call i8* @malloc(i64 16)
      %field = getelementptr i8, i8* %heap, i64 8
      call void @free(i8* %heap)
      call void @free(i8* %field)
      ret void
    }
  )IR", diagnostic, context);
  ASSERT_TRUE(module);
  const llvm::Value *heap = nullptr, *field = nullptr;
  for (const auto &instruction : module->getFunction("site")->getEntryBlock()) {
    if (instruction.getName() == "heap") heap = &instruction;
    if (instruction.getName() == "field") field = &instruction;
  }
  // 10 is the canonical object, 11 an unbound per-offset copy of the same
  // allocation, and 12 a field bound to the copy.
  lotus::analysis::SVFG svfg;
  svfg.setObjectValue(10, heap);
  svfg.setObjectValue(11, heap);
  lotus::analysis::SVFG::ObjectInfo fieldInfo;
  fieldInfo.baseObjId = 11;
  fieldInfo.fieldOffset = 8;
  fieldInfo.hasFieldOffset = true;
  svfg.setObjectInfo(12, fieldInfo);
  svfg.setObjectValue(12, field);
  svfg.setObjectsForValue(field, {12});

  auto merged = buildUseTraceSSAFromLotusSVFG(svfg, *module, NativeHistoryMode::DoubleFree);
  EXPECT_EQ(merged.graph.resourceCandidates(), std::vector<ObjectID>({10}));
  auto scan = DefectDetector(merged.graph, 3).scan(DefectKind::DoubleFree);
  ASSERT_EQ(scan.findings.size(), 1u);
  EXPECT_EQ(scan.findings.front().objects, std::vector<ObjectID>({10}));
  expectFixedMatchesBatch(merged.graph, DefectKind::DoubleFree);

  NativeHistoryOptions abstractObjects;
  abstractObjects.allocationSiteObjects = false;
  auto split = buildUseTraceSSAFromLotusSVFG(svfg, *module, NativeHistoryMode::DoubleFree,
                                             abstractObjects);
  EXPECT_EQ(split.graph.resourceCandidates(), std::vector<ObjectID>({10, 11, 12}));
  EXPECT_TRUE(DefectDetector(split.graph, 3).scan(DefectKind::DoubleFree).findings.empty());

  // Keys name the allocation by position and the field path, not by ID; the
  // unbound copy shares the canonical object's key.
  StableObjectKeys keys(svfg, *module);
  EXPECT_EQ(keys(10), "site:0");
  EXPECT_EQ(keys(11), "site:0");
  EXPECT_EQ(keys(12), "site:0+8");
  EXPECT_EQ(keys(99), "id:99");
  EXPECT_EQ(keys((ObjectID(1) << 63) + 2), "synthetic:2");
}

TEST(UseTraceSSANative, UnknownSourceKeepsNamedObjectCandidates) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare void @free(i8*)
    define i8 @check(i8* %released, i8* %used) {
      call void @free(i8* %released)
      %value = load i8, i8* %used
      ret i8 %value
    }
  )IR", diagnostic, context);
  ASSERT_TRUE(module);
  lotus::analysis::SVFG svfg;
  svfg.addObjectForValue(module->getFunction("check")->getArg(1), 99);
  auto result = buildUseTraceSSAFromLotusSVFG(svfg, *module, NativeHistoryMode::UseAfterFree);
  EXPECT_EQ(result.graph.resourceCandidates(), std::vector<ObjectID>({99, UnknownResource}));
  auto scan = DefectDetector(result.graph, 3).scan(DefectKind::UseAfterFree);
  ASSERT_EQ(scan.findings.size(), 1u);
  EXPECT_EQ(scan.findings.front().objects, std::vector<ObjectID>({99}));
}

TEST(UseTraceSSANative, MissingPropertySourcesAvoidResourceConstruction) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    define i8 @read_only(i8* %p) { %v = load i8, i8* %p ret i8 %v }
  )IR", diagnostic, context);
  ASSERT_TRUE(module);
  lotus::analysis::SVFG svfg;
  svfg.addObjectForValue(module->getFunction("read_only")->getArg(0), 99);
  for (auto mode : {NativeHistoryMode::DoubleFree, NativeHistoryMode::UseAfterFree,
                    NativeHistoryMode::MemoryLeak, NativeHistoryMode::FileLeak}) {
    auto result = buildUseTraceSSAFromLotusSVFG(svfg, *module, mode);
    EXPECT_TRUE(result.graph.nodes().empty());
    EXPECT_TRUE(result.graph.resourceCandidates().empty());
    EXPECT_FALSE(result.graph.complete());
  }
  EXPECT_FALSE(buildUseTraceSSAFromLotusSVFG(svfg, *module, NativeHistoryMode::Full)
                   .graph.nodes().empty());
}
TEST(UseTraceSSANative, MultipleCallersCannotExchangeReturns) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto m=llvm::parseAssemblyString(R"IR(
    declare void @free(i8*)
    define void @shared() { ret void }
    define void @one(i8* %p) {
      call void @free(i8* %p)
      call void @shared()
      ret void
    }
    define i8 @two(i8* %p) {
      call void @shared()
      %v = load i8, i8* %p
      ret i8 %v
    }
  )IR",diagnostic,context);
  ASSERT_TRUE(m); lotus::analysis::SVFG svfg;
  for (const char *name : {"one","two"})
    svfg.addObjectForValue(m->getFunction(name)->getArg(0),42);
  auto r=buildUseTraceSSAFromLotusSVFG(svfg,*m,NativeHistoryMode::UseAfterFree);
  auto q=queries::useAfterFree(r.graph); q.memoryObject=42;
  EXPECT_FALSE(QueryEngine(r.graph).run(q).found());
  q.memoryObject.reset();
  EXPECT_FALSE(QueryEngine(r.graph).runObjects({q,ObjectUniverse({42})}).found.any());
  q.context=ContextMode::Insensitive;
  EXPECT_TRUE(QueryEngine(r.graph).runObjects({q,ObjectUniverse({42})}).found.any());
}
TEST(UseTraceSSANative, NoReturnAndRecursion) {
  llvm::LLVMContext context; llvm::SMDiagnostic diagnostic;
  auto m=llvm::parseAssemblyString(R"IR(
    declare void @free(i8*)
    define void @stop() { unreachable }
    define i8 @caller(i8* %p) {
      call void @free(i8* %p)
      call void @stop()
      %v = load i8, i8* %p
      ret i8 %v
    }
    define void @recursive(i8* %p, i1 %condition) {
      br i1 %condition, label %base, label %recur
    recur:
      call void @recursive(i8* %p, i1 true)
      ret void
    base:
      call void @free(i8* %p)
      ret void
    }
    define i8 @after_recursion(i8* %p) {
      call void @recursive(i8* %p, i1 false)
      %v = load i8, i8* %p
      ret i8 %v
    }
  )IR",diagnostic,context);
  ASSERT_TRUE(m); lotus::analysis::SVFG svfg;
  svfg.addObjectForValue(m->getFunction("caller")->getArg(0),1);
  for (const char *name : {"recursive","after_recursion"})
    svfg.addObjectForValue(m->getFunction(name)->getArg(0),2);
  for (auto mode : {NativeHistoryMode::Full,NativeHistoryMode::UseAfterFree}) {
    auto r=buildUseTraceSSAFromLotusSVFG(svfg,*m,mode);
    auto q=queries::useAfterFree(r.graph);
    auto batch=QueryEngine(r.graph).runObjects({q,ObjectUniverse({1,2})});
    EXPECT_NE(batch.status(1),QueryStatus::Found);
    EXPECT_EQ(batch.status(2),QueryStatus::Found);
    for (ObjectID object : {1,2}) {
      q.memoryObject=object;
      EXPECT_EQ(batch.status(object),QueryEngine(r.graph).run(q).status);
    }
  }
}
TEST(UseTraceSSANative, ContractsEmptyChainsAndRetainsDeadCycles) {
  std::ostringstream text;
  text << "declare void @free(i8*)\ndefine i8 @f(i8* %p) {\nentry:\n"
          "call void @free(i8* %p)\nbr label %b0\n";
  for (unsigned i = 0; i < 100; ++i)
    text << "b" << i << ":\nbr label %"
         << (i == 99 ? "done" : "b" + std::to_string(i + 1)) << '\n';
  text << "done:\n%v = load i8, i8* %p\nret i8 %v\n"
          "dead1:\nbr label %dead2\ndead2:\nbr label %dead1\n}\n";
  llvm::LLVMContext context;
  llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(text.str(), diagnostic, context);
  ASSERT_TRUE(module);
  lotus::analysis::SVFG svfg;
  auto result = buildUseTraceSSAFromLotusSVFG(svfg, *module,
                                              NativeHistoryMode::UseAfterFree);
  ASSERT_TRUE(result.graph.verify());
  const auto &history =
      result.graph.layer(result.graph.nodes().front().function).history;
  EXPECT_EQ(history.program().blocks().size(), 3u);
  EXPECT_TRUE(DefectDetector(result.graph)
                  .run(DefectKind::UseAfterFree)
                  .result.found());
}

TEST(UseTraceSSANative, CompactLabelsAndSparseProvenancePreserveEffects) {
  llvm::LLVMContext context;
  llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare void @free(i8*)
    define i8 @f(i8* %p) {
      %unrelated = add i32 1, 2
      call void @free(i8* %p)
      %v = load i8, i8* %p
      ret i8 %v
    }
  )IR",
                                          diagnostic, context);
  ASSERT_TRUE(module);
  auto &block = module->getFunction("f")->getEntryBlock();
  auto it = block.begin();
  auto *unrelated = &*it++;
  auto *release = &*it++;
  auto *use = &*it;
  lotus::analysis::SVFG svfg;
  svfg.addNode(new lotus::analysis::CopySVFGNode(1, nullptr, unrelated));
  svfg.addNode(new lotus::analysis::CopySVFGNode(90, nullptr, release));
  svfg.addNode(new lotus::analysis::CopySVFGNode(50, nullptr, release));
  svfg.addNode(new lotus::analysis::CopySVFGNode(99, nullptr, use));
  auto full = buildUseTraceSSAFromLotusSVFG(svfg, *module,
                                            NativeHistoryMode::UseAfterFree);
  auto compact = buildUseTraceSSAFromLotusSVFG(
      svfg, *module, NativeHistoryMode::UseAfterFree, {false});
  ASSERT_EQ(full.graph.nodes().size(), compact.graph.nodes().size());
  EXPECT_EQ(full.graph.edges().size(), compact.graph.edges().size());
  for (std::size_t i = 0; i < full.graph.nodes().size(); ++i) {
    EXPECT_EQ(full.graph.node(i).native, compact.graph.node(i).native);
    EXPECT_EQ(full.graph.node(i).effects.size(),
              compact.graph.node(i).effects.size());
  }
  auto report = DefectDetector(compact.graph).run(DefectKind::UseAfterFree);
  ASSERT_TRUE(report.result.found());
  EXPECT_EQ(report.nativeWitness, std::vector<NativeID>({50, 99}));
  EXPECT_EQ(compact.graph.node(report.sink).label.find("load i8"),
            std::string::npos);
}

TEST(UseTraceSSANative, UnreachableIsNotANormalLeakExit) {
  llvm::LLVMContext context;
  llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseAssemblyString(R"IR(
    declare i8* @malloc(i64)
    define void @f() {
      %p = call i8* @malloc(i64 8)
      unreachable
    }
  )IR",
                                          diagnostic, context);
  ASSERT_TRUE(module);
  lotus::analysis::SVFG svfg;
  auto result = buildUseTraceSSAFromLotusSVFG(svfg, *module,
                                              NativeHistoryMode::MemoryLeak);
  EXPECT_TRUE(result.graph.select(Event::Exit).empty());
  EXPECT_EQ(DefectDetector(result.graph).scan(DefectKind::MemoryLeak).status,
            QueryStatus::Unknown);
}
} // namespace
