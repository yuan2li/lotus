#include "Analysis/ControlDependence/ControlDependence.h"

#include "llvm/AsmParser/Parser.h"
#include "llvm/IR/BasicBlock.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/SourceMgr.h"

#include "Analysis/ControlDependence/CompactControlDependence.h"
#include "Analysis/ControlDependence/ControlClosure.h"
#include "Analysis/ControlDependence/DOD.h"
#include "Analysis/ControlDependence/ICFGControlDependence.h"
#include "Analysis/ControlDependence/Reducibility.h"
#include "IR/ICFG/ICFG.h"
#include "IR/ICFG/ICFGBuilder.h"

#include <algorithm>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <random>
#include <set>
#include <tuple>
#include <vector>

#include <gtest/gtest.h>

using lotus::cd::Algorithm;
using lotus::cd::ControlDependenceAnalysis;
using lotus::cd::ControlDependenceOptions;
using lotus::cd::ICFGControlDependenceAnalysis;

namespace {

std::unique_ptr<llvm::Module> parseModule(llvm::LLVMContext &context,
                                          llvm::StringRef ir) {
  llvm::SMDiagnostic error;
  auto module = llvm::parseAssemblyString(ir, error, context);
  if (!module)
    error.print("ControlDependenceTest", llvm::errs());
  return module;
}

llvm::BasicBlock *block(llvm::Function &function, llvm::StringRef name) {
  for (llvm::BasicBlock &candidate : function)
    if (candidate.getName() == name)
      return &candidate;
  return nullptr;
}

ControlDependenceAnalysis analyze(llvm::Function &function,
                                  Algorithm algorithm) {
  return ControlDependenceAnalysis(function,
                                   ControlDependenceOptions{algorithm});
}

using Triple = std::tuple<unsigned, unsigned, unsigned>;

bool reachableBefore(lotus::cd::detail::GraphNode *start,
                     lotus::cd::detail::GraphNode *target,
                     lotus::cd::detail::GraphNode *forbidden,
                     size_t nodeCount) {
  if (start == forbidden)
    return false;
  if (start == target)
    return true;
  std::vector<bool> seen(nodeCount + 1, false);
  std::vector<lotus::cd::detail::GraphNode *> worklist{start};
  seen[start->getID()] = true;
  while (!worklist.empty()) {
    auto *node = worklist.back();
    worklist.pop_back();
    for (auto *successor : node->successors()) {
      if (successor == forbidden)
        continue;
      if (successor == target)
        return true;
      if (!seen[successor->getID()]) {
        seen[successor->getID()] = true;
        worklist.push_back(successor);
      }
    }
  }
  return false;
}

bool bruteInevitable(lotus::cd::detail::Graph &graph,
                     lotus::cd::detail::GraphNode *source,
                     lotus::cd::detail::GraphNode *target) {
  if (source == target)
    return true;

  std::vector<bool> reachable(graph.size() + 1, false);
  std::vector<lotus::cd::detail::GraphNode *> worklist{source};
  reachable[source->getID()] = true;
  while (!worklist.empty()) {
    auto *node = worklist.back();
    worklist.pop_back();
    if (node->successors().empty())
      return false; // A finite maximal path avoids target.
    for (auto *successor : node->successors()) {
      if (successor == target || reachable[successor->getID()])
        continue;
      reachable[successor->getID()] = true;
      worklist.push_back(successor);
    }
  }

  // A reachable cycle in G-{target} gives an infinite maximal path avoiding
  // target. Absence of both such a cycle and a reachable sink forces target.
  std::vector<unsigned char> color(graph.size() + 1, 0);
  std::function<bool(lotus::cd::detail::GraphNode *)> hasCycle =
      [&](auto *node) {
        color[node->getID()] = 1;
        for (auto *successor : node->successors()) {
          if (successor == target || !reachable[successor->getID()])
            continue;
          if (color[successor->getID()] == 1)
            return true;
          if (color[successor->getID()] == 0 && hasCycle(successor))
            return true;
        }
        color[node->getID()] = 2;
        return false;
      };
  for (auto *node : graph.nodes())
    if (reachable[node->getID()] && color[node->getID()] == 0 && hasCycle(node))
      return false;
  return true;
}

std::set<Triple>
bruteDOD(lotus::cd::detail::Graph &graph,
         const lotus::cd::detail::Inevitability &inevitability) {
  std::set<Triple> result;
  for (auto *decision : graph.predicates()) {
    if (decision->successors().size() != 2)
      continue;
    auto *firstSuccessor = decision->successors()[0];
    auto *secondSuccessor = decision->successors()[1];
    for (unsigned firstID = 1; firstID <= graph.size(); ++firstID) {
      auto *first = graph.getNode(firstID);
      if (first == decision || !inevitability.contains(decision, first))
        continue;
      for (unsigned secondID = firstID + 1; secondID <= graph.size();
           ++secondID) {
        auto *second = graph.getNode(secondID);
        if (second == decision || !inevitability.contains(decision, second))
          continue;
        bool firstBeforeSecond1 =
            !reachableBefore(firstSuccessor, second, first, graph.size());
        bool secondBeforeFirst1 =
            !reachableBefore(firstSuccessor, first, second, graph.size());
        bool firstBeforeSecond2 =
            !reachableBefore(secondSuccessor, second, first, graph.size());
        bool secondBeforeFirst2 =
            !reachableBefore(secondSuccessor, first, second, graph.size());
        if ((firstBeforeSecond1 && secondBeforeFirst2) ||
            (secondBeforeFirst1 && firstBeforeSecond2))
          result.insert({decision->getID(), firstID, secondID});
      }
    }
  }
  return result;
}

std::set<Triple>
enumerateCompactDOD(const lotus::cd::detail::Graph &graph,
                    const lotus::cd::detail::DODBicliqueMap &bicliques) {
  std::set<Triple> result;
  lotus::cd::detail::forEachDODPair(
      graph, bicliques, [&](auto *decision, auto *first, auto *second) {
        result.insert({decision->getID(), first->getID(), second->getID()});
      });
  return result;
}

constexpr llvm::StringLiteral DiamondIR = R"(
  define void @diamond(i1 %condition) {
  entry:
    br i1 %condition, label %left, label %right
  left:
    br label %exit
  right:
    br label %exit
  exit:
    ret void
  }
)";

constexpr llvm::StringLiteral NonterminatingChoiceIR = R"(
  define void @nonterminating_choice(i1 %condition) {
  entry:
    br i1 %condition, label %left, label %right
  left:
    br label %left
  right:
    br label %right
  }
)";

constexpr llvm::StringLiteral DecisiveOrderIR = R"(
  define void @decisive_order(i1 %condition) {
  entry:
    br i1 %condition, label %blue, label %red
  blue:
    br label %blue_body
  blue_body:
    br label %red
  red:
    br label %red_body
  red_body:
    br label %blue
  }
)";

TEST(ControlDependenceTest, StandardComputesDiamondDependencesAndInverse) {
  llvm::LLVMContext context;
  auto module = parseModule(context, DiamondIR);
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("diamond");
  auto analysis = analyze(function, Algorithm::Standard);

  llvm::BasicBlock *entry = block(function, "entry");
  llvm::BasicBlock *left = block(function, "left");
  llvm::BasicBlock *right = block(function, "right");
  llvm::BasicBlock *exit = block(function, "exit");
  ASSERT_NE(entry, nullptr);
  ASSERT_NE(left, nullptr);
  ASSERT_NE(right, nullptr);
  ASSERT_NE(exit, nullptr);

  EXPECT_TRUE(analysis.dependsOn(left, entry));
  EXPECT_TRUE(analysis.dependsOn(right, entry));
  EXPECT_FALSE(analysis.dependsOn(exit, entry));
  ASSERT_EQ(analysis.getDependents(entry).size(), 2u);
  EXPECT_EQ(analysis.getDependents(entry)[0], left);
  EXPECT_EQ(analysis.getDependents(entry)[1], right);
}

TEST(ControlDependenceTest, StandardMatchesDgOnLoopsAndMultipleExits) {
  llvm::LLVMContext context;
  auto module = parseModule(context, R"(
    define void @loop(i1 %condition) {
    entry:
      br label %header
    header:
      br i1 %condition, label %body, label %exit
    body:
      br label %header
    exit:
      ret void
    }

    define void @multiple_exits(i1 %condition) {
    entry:
      br i1 %condition, label %left, label %right
    left:
      ret void
    right:
      ret void
    }
  )");
  ASSERT_TRUE(module);

  llvm::Function &loop = *module->getFunction("loop");
  llvm::BasicBlock *header = block(loop, "header");
  llvm::BasicBlock *body = block(loop, "body");
  auto loopAnalysis = analyze(loop, Algorithm::Standard);
  EXPECT_TRUE(loopAnalysis.dependsOn(body, header));
  EXPECT_TRUE(loopAnalysis.dependsOn(header, header));

  llvm::Function &multipleExits = *module->getFunction("multiple_exits");
  llvm::BasicBlock *entry = block(multipleExits, "entry");
  llvm::BasicBlock *left = block(multipleExits, "left");
  llvm::BasicBlock *right = block(multipleExits, "right");
  auto exitAnalysis = analyze(multipleExits, Algorithm::Standard);
  EXPECT_TRUE(exitAnalysis.dependsOn(left, entry));
  EXPECT_TRUE(exitAnalysis.dependsOn(right, entry));
}

TEST(ControlDependenceTest, NTSCDImplementationsAgreeOnDiamond) {
  llvm::LLVMContext context;
  auto module = parseModule(context, DiamondIR);
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("diamond");
  llvm::BasicBlock *entry = block(function, "entry");
  llvm::BasicBlock *left = block(function, "left");
  llvm::BasicBlock *right = block(function, "right");

  for (Algorithm algorithm :
       {Algorithm::NTSCD, Algorithm::NTSCD2, Algorithm::NTSCDLegacy,
        Algorithm::NTSCDRanganath, Algorithm::NTSCDRanganathOriginal}) {
    auto analysis = analyze(function, algorithm);
    EXPECT_TRUE(analysis.dependsOn(left, entry));
    EXPECT_TRUE(analysis.dependsOn(right, entry));
  }
}

TEST(ControlDependenceTest, NTSCDHandlesFunctionsWithoutExits) {
  llvm::LLVMContext context;
  auto module = parseModule(context, NonterminatingChoiceIR);
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("nonterminating_choice");
  llvm::BasicBlock *entry = block(function, "entry");
  llvm::BasicBlock *left = block(function, "left");
  llvm::BasicBlock *right = block(function, "right");

  auto ntscd = analyze(function, Algorithm::NTSCD);
  auto ntscd2 = analyze(function, Algorithm::NTSCD2);
  EXPECT_TRUE(ntscd.dependsOn(left, entry));
  EXPECT_TRUE(ntscd.dependsOn(right, entry));
  EXPECT_TRUE(ntscd2.dependsOn(left, entry));
  EXPECT_TRUE(ntscd2.dependsOn(right, entry));
}

TEST(ControlDependenceTest, NTSCD2DoesNotReenqueueColoredSelfLoopTarget) {
  llvm::LLVMContext context;
  auto module = parseModule(context, R"(
    define void @self_loop_target(i1 %condition) {
    entry:
      br i1 %condition, label %loop, label %entry
    loop:
      br label %loop
    }
  )");
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("self_loop_target");
  llvm::BasicBlock *entry = block(function, "entry");
  llvm::BasicBlock *loop = block(function, "loop");

  auto ntscd2 = analyze(function, Algorithm::NTSCD2);
  auto combined = analyze(function, Algorithm::DODNTSCD);
  EXPECT_TRUE(ntscd2.dependsOn(loop, entry));
  EXPECT_TRUE(combined.dependsOn(loop, entry));
}

TEST(ControlDependenceTest, CompactNTSCDSupportsMultiwayDecisions) {
  llvm::LLVMContext context;
  auto module = parseModule(context, R"(
    define void @multiway(i32 %selector) {
    entry:
      switch i32 %selector, label %avoid [
        i32 0, label %first
        i32 1, label %second
      ]
    first:
      br label %target
    second:
      br label %target
    avoid:
      br label %exit
    target:
      br label %exit
    exit:
      ret void
    }
  )");
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("multiway");
  llvm::BasicBlock *entry = block(function, "entry");
  llvm::BasicBlock *target = block(function, "target");
  auto analysis = analyze(function, Algorithm::NTSCDCompact);
  EXPECT_TRUE(analysis.dependsOn(target, entry));
}

TEST(ControlDependenceTest, DODFindsDecisiveOrderOnInfiniteCycle) {
  llvm::LLVMContext context;
  auto module = parseModule(context, DecisiveOrderIR);
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("decisive_order");
  llvm::BasicBlock *entry = block(function, "entry");
  llvm::BasicBlock *blueBody = block(function, "blue_body");
  llvm::BasicBlock *redBody = block(function, "red_body");

  auto dod = analyze(function, Algorithm::DOD);
  auto ranganath = analyze(function, Algorithm::DODRanganath);
  auto combined = analyze(function, Algorithm::DODNTSCD);
  EXPECT_TRUE(dod.dependsOn(blueBody, entry));
  EXPECT_TRUE(dod.dependsOn(redBody, entry));
  EXPECT_TRUE(ranganath.dependsOn(blueBody, entry));
  EXPECT_TRUE(ranganath.dependsOn(redBody, entry));
  EXPECT_TRUE(combined.dependsOn(blueBody, entry));
  EXPECT_TRUE(combined.dependsOn(redBody, entry));
}

TEST(ControlDependenceTest, CompactDODExposesCanonicalBicliqueAndClosure) {
  llvm::LLVMContext context;
  auto module = parseModule(context, DecisiveOrderIR);
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("decisive_order");
  llvm::BasicBlock *entry = block(function, "entry");
  llvm::BasicBlock *blue = block(function, "blue");
  llvm::BasicBlock *blueBody = block(function, "blue_body");
  llvm::BasicBlock *red = block(function, "red");
  llvm::BasicBlock *redBody = block(function, "red_body");

  auto analysis = analyze(function, Algorithm::DODNTSCDCompact);
  ASSERT_TRUE(analysis.hasDODBiclique(entry));
  EXPECT_EQ(analysis.getDODLeft(entry),
            (ControlDependenceAnalysis::BlockVector{blue, blueBody}));
  EXPECT_EQ(analysis.getDODRight(entry),
            (ControlDependenceAnalysis::BlockVector{red, redBody}));
  EXPECT_TRUE(analysis.isDOD(entry, blueBody, redBody));
  EXPECT_FALSE(analysis.isDOD(entry, blue, blueBody));
  EXPECT_TRUE(analysis.dependsOn(blueBody, entry));
  EXPECT_TRUE(analysis.dependsOn(redBody, entry));

  auto closure = analysis.getDependencyClosure({blueBody, redBody});
  EXPECT_NE(std::find(closure.begin(), closure.end(), entry), closure.end());
}

TEST(ControlDependenceTest, BaselineAndCompactStreamTheSameExactDODPairs) {
  lotus::cd::detail::Graph graph;
  std::vector<lotus::cd::detail::GraphNode *> nodes;
  for (unsigned index = 0; index < 5; ++index)
    nodes.push_back(&graph.createNode());
  graph.addEdge(*nodes[0], *nodes[1]);
  graph.addEdge(*nodes[0], *nodes[3]);
  graph.addEdge(*nodes[1], *nodes[2]);
  graph.addEdge(*nodes[2], *nodes[3]);
  graph.addEdge(*nodes[3], *nodes[4]);
  graph.addEdge(*nodes[4], *nodes[1]);

  auto inevitability = lotus::cd::detail::computeInevitability(graph);
  auto bicliques = lotus::cd::detail::computeCompactDOD(graph, inevitability);
  std::set<Triple> compact = enumerateCompactDOD(graph, bicliques);
  std::set<Triple> baseline;
  size_t baselineCount = 0;
  lotus::cd::detail::forEachBaselineDODPair(
      graph, [&](auto *decision, auto *first, auto *second) {
        ++baselineCount;
        unsigned firstID = std::min(first->getID(), second->getID());
        unsigned secondID = std::max(first->getID(), second->getID());
        baseline.insert({decision->getID(), firstID, secondID});
      });
  EXPECT_EQ(baseline, compact);
  EXPECT_EQ(baselineCount, compact.size());
  EXPECT_EQ(compact.size(), 4u);
}

TEST(ControlDependenceTest, EveryBinaryAlgorithmMaintainsInverseRelation) {
  llvm::LLVMContext context;
  auto module = parseModule(context, DecisiveOrderIR);
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("decisive_order");

  for (Algorithm algorithm :
       {Algorithm::Standard, Algorithm::NTSCD, Algorithm::NTSCD2,
        Algorithm::NTSCDLegacy, Algorithm::NTSCDRanganath,
        Algorithm::NTSCDRanganathOriginal, Algorithm::DOD,
        Algorithm::DODRanganath, Algorithm::DODNTSCD, Algorithm::NTSCDCompact,
        Algorithm::DODCompact, Algorithm::DODNTSCDCompact}) {
    auto analysis = analyze(function, algorithm);
    for (llvm::BasicBlock &dependent : function)
      for (const llvm::BasicBlock *predicate :
           analysis.getDependencies(&dependent)) {
        auto inverse = analysis.getDependents(predicate);
        EXPECT_NE(std::find(inverse.begin(), inverse.end(), &dependent),
                  inverse.end());
      }
  }
}

TEST(ControlDependenceTest, StrongClosureIsStableAndFunctionOrdered) {
  llvm::LLVMContext context;
  auto module = parseModule(context, R"(
    define void @closure(i1 %condition) {
    b2:
      br label %b3
    b3:
      br i1 %condition, label %b0, label %b1
    b1:
      br label %b0
    b0:
      br label %b0
    }
  )");
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("closure");
  llvm::BasicBlock *b2 = block(function, "b2");
  llvm::BasicBlock *b3 = block(function, "b3");
  llvm::BasicBlock *b1 = block(function, "b1");

  auto analysis = analyze(function, Algorithm::StrongControlClosure);
  auto closure = analysis.getClosure({b1, b2, b1});
  ASSERT_EQ(closure.size(), 3u);
  EXPECT_EQ(closure[0], b2);
  EXPECT_EQ(closure[1], b3);
  EXPECT_EQ(closure[2], b1);

  auto closedAgain = analysis.getClosure(closure);
  EXPECT_EQ(closedAgain, closure);
  EXPECT_TRUE(analysis.getDependencies(b1).empty());
}

TEST(ControlDependenceTest, StrongClosureHandlesColoredTargetCycles) {
  llvm::LLVMContext context;
  auto module = parseModule(context, R"(
    define void @closure_cycle(i1 %condition) {
    start:
      br label %predicate
    predicate:
      br i1 %condition, label %loop, label %predicate
    loop:
      br label %loop
    }
  )");
  ASSERT_TRUE(module);
  llvm::Function &function = *module->getFunction("closure_cycle");
  llvm::BasicBlock *start = block(function, "start");
  llvm::BasicBlock *predicate = block(function, "predicate");
  llvm::BasicBlock *loop = block(function, "loop");

  auto analysis = analyze(function, Algorithm::StrongControlClosure);
  auto closure = analysis.getClosure({start, loop});
  ASSERT_EQ(closure.size(), 3u);
  EXPECT_EQ(closure[0], start);
  EXPECT_EQ(closure[1], predicate);
  EXPECT_EQ(closure[2], loop);
}

TEST(ControlDependenceTest,
     CompactDODAndClosureMatchDefinitionsOnAllThreeNodeGraphs) {
  constexpr unsigned nodeCount = 3;
  constexpr unsigned graphCount = 1u << (nodeCount * nodeCount);
  for (unsigned mask = 0; mask < graphCount; ++mask) {
    lotus::cd::detail::Graph graph;
    std::vector<lotus::cd::detail::GraphNode *> nodes;
    for (unsigned index = 0; index < nodeCount; ++index)
      nodes.push_back(&graph.createNode());
    for (unsigned source = 0; source < nodeCount; ++source)
      for (unsigned target = 0; target < nodeCount; ++target)
        if (mask & (1u << (source * nodeCount + target)))
          graph.addEdge(*nodes[source], *nodes[target]);

    auto inevitability = lotus::cd::detail::computeInevitability(graph);
    for (auto *source : graph.nodes())
      for (auto *target : graph.nodes())
        EXPECT_EQ(inevitability.contains(source, target),
                  bruteInevitable(graph, source, target))
            << "graph mask " << mask << ", source " << source->getID()
            << ", target " << target->getID();
    auto ntscd = lotus::cd::detail::computeCompactNTSCD(graph, inevitability);
    auto bicliques = lotus::cd::detail::computeCompactDOD(graph, inevitability);
    auto exactSetBicliques =
        lotus::cd::detail::computeCompactDODExactSets(graph, inevitability);
    std::set<Triple> expectedDOD = bruteDOD(graph, inevitability);
    std::set<Triple> compactDOD = enumerateCompactDOD(graph, bicliques);
    EXPECT_EQ(compactDOD, expectedDOD) << "graph mask " << mask;
    EXPECT_EQ(enumerateCompactDOD(graph, exactSetBicliques), expectedDOD)
        << "graph mask " << mask;

    for (unsigned seedMask = 0; seedMask < (1u << nodeCount); ++seedMask) {
      lotus::cd::detail::NodeSet seed;
      std::set<unsigned> expected;
      for (unsigned index = 0; index < nodeCount; ++index)
        if (seedMask & (1u << index)) {
          seed.insert(nodes[index]);
          expected.insert(index + 1);
        }

      bool changed;
      do {
        changed = false;
        for (const auto &entry : ntscd.first) {
          if (!expected.count(entry.first->getID()))
            continue;
          for (auto *decision : entry.second)
            changed |= expected.insert(decision->getID()).second;
        }
        for (const Triple &triple : expectedDOD) {
          unsigned decision;
          unsigned first;
          unsigned second;
          std::tie(decision, first, second) = triple;
          if (expected.count(first) && expected.count(second))
            changed |= expected.insert(decision).second;
        }
      } while (changed);

      auto closure = lotus::cd::detail::computeCompactDependencyClosure(
          graph, seed, ntscd, bicliques);
      auto eagerClosure = lotus::cd::detail::computeEagerPairDependencyClosure(
          graph, seed, ntscd, bicliques);
      std::set<unsigned> actual;
      for (auto *node : closure)
        actual.insert(node->getID());
      EXPECT_EQ(actual, expected)
          << "graph mask " << mask << ", seed mask " << seedMask;
      std::set<unsigned> eagerActual;
      for (auto *node : eagerClosure)
        eagerActual.insert(node->getID());
      EXPECT_EQ(eagerActual, expected)
          << "graph mask " << mask << ", seed mask " << seedMask;
    }
  }
}

TEST(ControlDependenceTest, CompactDODMatchesDefinitionOnAllFourNodeGraphs) {
  constexpr unsigned nodeCount = 4;
  constexpr unsigned graphCount = 1u << (nodeCount * nodeCount);
  for (unsigned mask = 0; mask < graphCount; ++mask) {
    lotus::cd::detail::Graph graph;
    std::vector<lotus::cd::detail::GraphNode *> nodes;
    for (unsigned index = 0; index < nodeCount; ++index)
      nodes.push_back(&graph.createNode());
    for (unsigned source = 0; source < nodeCount; ++source)
      for (unsigned target = 0; target < nodeCount; ++target)
        if (mask & (1u << (source * nodeCount + target)))
          graph.addEdge(*nodes[source], *nodes[target]);

    auto inevitability = lotus::cd::detail::computeInevitability(graph);
    auto bicliques = lotus::cd::detail::computeCompactDOD(graph, inevitability);
    ASSERT_EQ(enumerateCompactDOD(graph, bicliques),
              bruteDOD(graph, inevitability))
        << "graph mask " << mask;
  }
}

TEST(ControlDependenceTest, ICFGAdapterRunsGraphAlgorithmsOnLotusICFG) {
  llvm::LLVMContext context;
  auto module = parseModule(context, R"(
    define i32 @main(i1 %condition) {
    entry:
      br i1 %condition, label %left, label %right
    left:
      br label %exit
    right:
      br label %exit
    exit:
      ret i32 0
    }
  )");
  ASSERT_TRUE(module);

  ICFG icfg;
  ICFGBuilder builder(&icfg);
  builder.build(module.get());
  llvm::Function &function = *module->getFunction("main");
  ICFGNode *entry = icfg.getIntraBlockNode(block(function, "entry"));
  ICFGNode *left = icfg.getIntraBlockNode(block(function, "left"));
  ICFGNode *right = icfg.getIntraBlockNode(block(function, "right"));

  ICFGControlDependenceAnalysis analysis(
      icfg, ControlDependenceOptions{Algorithm::NTSCD2});
  EXPECT_TRUE(analysis.dependsOn(left, entry));
  EXPECT_TRUE(analysis.dependsOn(right, entry));
  ASSERT_EQ(analysis.getDependents(entry).size(), 2u);
  EXPECT_LT(analysis.getDependents(entry)[0]->getId(),
            analysis.getDependents(entry)[1]->getId());
}

TEST(ControlDependenceTest, ICFGAdapterFindsNoReturnCallDependence) {
  llvm::LLVMContext context;
  auto module = parseModule(context, R"(
    declare void @abort() noreturn

    define void @foo(i1 %condition) {
    entry:
      br i1 %condition, label %normal, label %die
    normal:
      ret void
    die:
      call void @abort()
      unreachable
    }

    define i32 @main(i1 %condition) {
    entry:
      call void @foo(i1 %condition)
      br label %after
    after:
      ret i32 0
    }
  )");
  ASSERT_TRUE(module);

  ICFG icfg;
  ICFGBuilder builder(&icfg);
  builder.build(module.get());
  llvm::Function &foo = *module->getFunction("foo");
  llvm::Function &main = *module->getFunction("main");
  ICFGNode *fooEntry = icfg.getIntraBlockNode(block(foo, "entry"));
  ICFGNode *after = icfg.getIntraBlockNode(block(main, "after"));

  ICFGControlDependenceAnalysis analysis(
      icfg, ControlDependenceOptions{Algorithm::NTSCD});
  EXPECT_TRUE(analysis.dependsOn(after, fooEntry));
}

TEST(ControlDependenceTest, ICFGAdapterExposesCompactDODBicliques) {
  llvm::LLVMContext context;
  auto module = parseModule(context, DecisiveOrderIR);
  ASSERT_TRUE(module);
  ICFG icfg;
  ICFGBuilder builder(&icfg);
  builder.build(module.get());

  llvm::Function &function = *module->getFunction("decisive_order");
  ICFGNode *entry = icfg.getIntraBlockNode(block(function, "entry"));
  ICFGNode *blueBody = icfg.getIntraBlockNode(block(function, "blue_body"));
  ICFGNode *redBody = icfg.getIntraBlockNode(block(function, "red_body"));
  ICFGControlDependenceAnalysis analysis(
      icfg, ControlDependenceOptions{Algorithm::DODNTSCDCompact});

  EXPECT_TRUE(analysis.hasDODBiclique(entry));
  EXPECT_TRUE(analysis.isDOD(entry, blueBody, redBody));
  auto closure = analysis.getDependencyClosure({blueBody, redBody});
  EXPECT_NE(std::find(closure.begin(), closure.end(), entry), closure.end());
}

// Helpers for the strong-closure differential tests below.
std::set<unsigned> closureIDs(const lotus::cd::detail::NodeSet &nodes) {
  std::set<unsigned> result;
  for (auto *node : nodes)
    result.insert(node->getID());
  return result;
}

// Whether every vertex is reachable from the start, which is the hypothesis
// the rooted strong-closure corollary requires.
bool allReachableFromStart(lotus::cd::detail::Graph &graph,
                           lotus::cd::detail::GraphNode *start) {
  std::set<lotus::cd::detail::GraphNode *> seen{start};
  std::vector<lotus::cd::detail::GraphNode *> stack{start};
  while (!stack.empty()) {
    auto *node = stack.back();
    stack.pop_back();
    for (auto *successor : node->successors())
      if (seen.insert(successor).second)
        stack.push_back(successor);
  }
  return seen.size() == graph.size();
}

// The evaluation pairs SOTA-Closure with Full-Closure and reports their times
// as like-for-like, which presumes they return the same set. Nothing tested
// that: the C++ suite only compared the compact closure against Eager-Pairs,
// and the reference validator only compared it against explicit pair closure,
// so both sides of every existing check were this paper's own semantics.
TEST(ControlDependenceTest, StrongAndCompactClosureAgreeOnReachableGraphs) {
  constexpr unsigned nodeCount = 4;
  constexpr unsigned graphCount = 1u << (nodeCount * nodeCount);
  unsigned reachableGraphs = 0;
  unsigned graphsWithOrderRelation = 0;
  unsigned comparisons = 0;

  for (unsigned mask = 0; mask < graphCount; ++mask) {
    lotus::cd::detail::Graph graph;
    std::vector<lotus::cd::detail::GraphNode *> nodes;
    for (unsigned index = 0; index < nodeCount; ++index)
      nodes.push_back(&graph.createNode());
    for (unsigned source = 0; source < nodeCount; ++source)
      for (unsigned target = 0; target < nodeCount; ++target)
        if (mask & (1u << (source * nodeCount + target)))
          graph.addEdge(*nodes[source], *nodes[target]);

    if (!allReachableFromStart(graph, nodes[0]))
      continue;
    ++reachableGraphs;

    auto inevitability = lotus::cd::detail::computeInevitability(graph);
    auto ntscd = lotus::cd::detail::computeCompactNTSCD(graph, inevitability);
    auto bicliques = lotus::cd::detail::computeCompactDOD(graph, inevitability);
    if (!bicliques.empty())
      ++graphsWithOrderRelation;

    for (unsigned seedMask = 0; seedMask < (1u << nodeCount); ++seedMask) {
      // The start is always part of the seed, matching the driver and the
      // corollary's rooted hypothesis.
      lotus::cd::detail::NodeSet seed;
      seed.insert(nodes[0]);
      for (unsigned index = 1; index < nodeCount; ++index)
        if (seedMask & (1u << index))
          seed.insert(nodes[index]);

      auto sota = lotus::cd::detail::computeStrongControlClosure(graph, seed);
      auto full = lotus::cd::detail::computeCompactDependencyClosure(
          graph, seed, ntscd, bicliques);
      ASSERT_EQ(closureIDs(sota), closureIDs(full))
          << "graph mask " << mask << ", seed mask " << seedMask;
      ++comparisons;
    }
  }

  EXPECT_GT(reachableGraphs, 0u);
  EXPECT_GT(comparisons, 0u);
  // Printed so the evaluation can cite the size of this differential sweep.
  std::cout << "[ SWEEP    ] " << graphCount << " graphs, " << reachableGraphs
            << " with all vertices reachable, " << graphsWithOrderRelation
            << " with a non-empty order relation, " << comparisons
            << " closure comparisons\n";
  // Without this the sweep could pass vacuously: the order relation is empty on
  // every reducible graph, so a suite that never reaches a non-empty one would
  // agree trivially and prove nothing about the biclique path.
  EXPECT_GT(graphsWithOrderRelation, 0u)
      << "no graph in the sweep had a non-empty order relation";
}

// The agreement above is conditional, not universal. dg's algorithm walks
// forward from the seed, so a decision that no path reaches is invisible to it,
// while the relation-based closure still admits it. Unreachable vertices are
// exactly what the corollary's reachable-start hypothesis excludes, and a CFG
// that reaches this analysis after dead-code elimination cannot contain them.
TEST(ControlDependenceTest, StrongClosureMissesUnreachableDecisions) {
  lotus::cd::detail::Graph graph;
  std::vector<lotus::cd::detail::GraphNode *> nodes;
  for (unsigned index = 0; index < 6; ++index)
    nodes.push_back(&graph.createNode());

  // 1 -> 2 -> 3 -> 1 is a cycle; 0 is the start; 4 and 5 are decisions that
  // enter the cycle at two different points but have no predecessor.
  graph.addEdge(*nodes[0], *nodes[1]);
  graph.addEdge(*nodes[1], *nodes[2]);
  graph.addEdge(*nodes[2], *nodes[3]);
  graph.addEdge(*nodes[3], *nodes[1]);
  graph.addEdge(*nodes[4], *nodes[1]);
  graph.addEdge(*nodes[4], *nodes[3]);
  graph.addEdge(*nodes[5], *nodes[1]);
  graph.addEdge(*nodes[5], *nodes[3]);

  ASSERT_FALSE(allReachableFromStart(graph, nodes[0]));

  auto inevitability = lotus::cd::detail::computeInevitability(graph);
  auto ntscd = lotus::cd::detail::computeCompactNTSCD(graph, inevitability);
  auto bicliques = lotus::cd::detail::computeCompactDOD(graph, inevitability);

  lotus::cd::detail::NodeSet seed;
  for (unsigned index : {0u, 1u, 3u})
    seed.insert(nodes[index]);

  auto sota = lotus::cd::detail::computeStrongControlClosure(graph, seed);
  auto full = lotus::cd::detail::computeCompactDependencyClosure(
      graph, seed, ntscd, bicliques);
  // Documented divergence: the compact closure is the larger set because it
  // admits the unreachable decisions that the forward walk never visits.
  EXPECT_LT(closureIDs(sota).size(), closureIDs(full).size());
}

// Reference checks for the reducibility-guard tests below. Vertices are
// numbered 1..n and `start` is the root, as in the driver.
std::vector<bool> reachableFrom(lotus::cd::detail::Graph &graph,
                                lotus::cd::detail::GraphNode *start) {
  std::vector<bool> seen(graph.size() + 1, false);
  std::vector<lotus::cd::detail::GraphNode *> stack{start};
  seen[start->getID()] = true;
  while (!stack.empty()) {
    auto *node = stack.back();
    stack.pop_back();
    for (auto *successor : node->successors())
      if (!seen[successor->getID()]) {
        seen[successor->getID()] = true;
        stack.push_back(successor);
      }
  }
  return seen;
}

// Whether the subgraph reachable from `start` is reducible: removing every edge
// u -> v whose target dominates its source leaves it acyclic (Hecht-Ullman).
bool reachablePartIsReducible(lotus::cd::detail::Graph &graph,
                              lotus::cd::detail::GraphNode *start) {
  unsigned n = graph.size();
  unsigned root = start->getID();
  std::vector<bool> reach = reachableFrom(graph, start);
  std::vector<std::vector<unsigned>> preds(n + 1);
  for (auto *node : graph.nodes())
    if (reach[node->getID()])
      for (auto *successor : node->successors())
        preds[successor->getID()].push_back(node->getID());

  // dominators[v][u] holds when u dominates v; iterate to the greatest fixed
  // point, starting from "every reachable vertex dominates v".
  std::vector<std::vector<bool>> dominators(n + 1, reach);
  dominators[root].assign(n + 1, false);
  dominators[root][root] = true;
  for (bool changed = true; changed;) {
    changed = false;
    for (unsigned v = 1; v <= n; ++v) {
      if (!reach[v] || v == root)
        continue;
      std::vector<bool> next(n + 1, true);
      for (unsigned pred : preds[v])
        for (unsigned u = 1; u <= n; ++u)
          next[u] = next[u] && dominators[pred][u];
      next[0] = false;
      next[v] = true;
      if (next != dominators[v]) {
        dominators[v] = next;
        changed = true;
      }
    }
  }

  std::vector<unsigned char> color(n + 1, 0);
  std::function<bool(unsigned)> hasForwardCycle = [&](unsigned u) {
    color[u] = 1;
    for (auto *successor : graph.getNode(u)->successors()) {
      unsigned v = successor->getID();
      if (dominators[u][v])
        continue; // A back edge.
      if (color[v] == 1 || (color[v] == 0 && hasForwardCycle(v)))
        return true;
    }
    color[u] = 2;
    return false;
  };
  for (unsigned v = 1; v <= n; ++v)
    if (reach[v] && color[v] == 0 && hasForwardCycle(v))
      return false;
  return true;
}

// Whether a binary decision that `start` cannot reach can itself reach a cycle.
// DOD at a decision needs two vertices met in opposite orders on its branches,
// which is impossible when everything the decision reaches is acyclic.
bool unreachableDecisionReachesCycle(lotus::cd::detail::Graph &graph,
                                     lotus::cd::detail::GraphNode *start) {
  std::vector<bool> reach = reachableFrom(graph, start);
  auto onCycle = [&](lotus::cd::detail::GraphNode *node) {
    for (auto *successor : node->successors())
      if (reachableFrom(graph, successor)[node->getID()])
        return true;
    return false;
  };
  for (auto *decision : graph.nodes()) {
    if (reach[decision->getID()] || decision->successors().size() != 2)
      continue;
    std::vector<bool> below = reachableFrom(graph, decision);
    for (auto *node : graph.nodes())
      if (below[node->getID()] && onCycle(node))
        return true;
  }
  return false;
}

size_t dependenceCount(const lotus::cd::detail::DependenceResult &result) {
  size_t count = 0;
  for (const auto &entry : result.first)
    count += entry.second.size();
  return count;
}

// A reducibility guard lets a client skip DOD when it is known to be empty. In
// this graph model (several sinks, no unique exit, and vertices the start
// cannot reach) the guard must also account for unreachable decisions: it holds
// when the part reachable from the start is reducible and no unreachable
// decision reaches a cycle. Check exhaustively that the guard implies an empty
// DOD under the definition, the compact algorithm, and both baseline entry
// points.
TEST(ControlDependenceTest, ReducibilityGuardImpliesEmptyDOD) {
  constexpr unsigned nodeCount = 4;
  constexpr unsigned graphCount = 1u << (nodeCount * nodeCount);
  unsigned guarded = 0;
  unsigned unguardedWithDOD = 0;
  unsigned reducibleReachablePartWithDOD = 0;

  for (unsigned mask = 0; mask < graphCount; ++mask) {
    lotus::cd::detail::Graph graph;
    std::vector<lotus::cd::detail::GraphNode *> nodes;
    for (unsigned index = 0; index < nodeCount; ++index)
      nodes.push_back(&graph.createNode());
    for (unsigned source = 0; source < nodeCount; ++source)
      for (unsigned target = 0; target < nodeCount; ++target)
        if (mask & (1u << (source * nodeCount + target)))
          graph.addEdge(*nodes[source], *nodes[target]);

    bool reducible = reachablePartIsReducible(graph, nodes[0]);
    bool guard = reducible && !unreachableDecisionReachesCycle(graph, nodes[0]);
    auto inevitability = lotus::cd::detail::computeInevitability(graph);
    std::set<Triple> definition = bruteDOD(graph, inevitability);
    if (!guard) {
      unguardedWithDOD += !definition.empty();
      reducibleReachablePartWithDOD += reducible && !definition.empty();
      continue;
    }

    ++guarded;
    ASSERT_TRUE(definition.empty()) << "graph mask " << mask;
    ASSERT_TRUE(
        lotus::cd::detail::computeCompactDOD(graph, inevitability).empty())
        << "graph mask " << mask;
    size_t baselinePairs = 0;
    lotus::cd::detail::forEachBaselineDODPair(
        graph, [&](auto *, auto *, auto *) { ++baselinePairs; });
    ASSERT_EQ(baselinePairs, 0u) << "graph mask " << mask;
    ASSERT_EQ(dependenceCount(lotus::cd::detail::computeDOD(graph)), 0u)
        << "graph mask " << mask;
  }

  std::cout << "[ SWEEP    ] " << graphCount << " graphs, " << guarded
            << " satisfy the reducibility guard, " << unguardedWithDOD
            << " others have a non-empty DOD, " << reducibleReachablePartWithDOD
            << " of those with a reducible reachable part\n";
  EXPECT_GT(guarded, 0u);
  // Without this the property could hold vacuously.
  EXPECT_GT(unguardedWithDOD, 0u)
      << "no graph in the sweep had a non-empty DOD";
  // The unreachable-decision clause is necessary: checking only the reachable
  // part would skip these graphs even though their DOD is non-empty.
  EXPECT_GT(reducibleReachablePartWithDOD, 0u);
}

// The smallest witness for that clause. The reachable part 1 -> 2 <-> 3 is
// reducible, but vertex 4, which the start cannot reach, enters the loop at
// both 2 and 3 and so orders them decisively. A guard that inspects only the
// reachable part, such as LLVM's containsIrreducibleCFG, would skip this DOD.
TEST(ControlDependenceTest,
     ReachableOnlyReducibilityGuardMissesUnreachableDecisions) {
  lotus::cd::detail::Graph graph;
  std::vector<lotus::cd::detail::GraphNode *> nodes;
  for (unsigned index = 0; index < 4; ++index)
    nodes.push_back(&graph.createNode());
  graph.addEdge(*nodes[0], *nodes[1]);
  graph.addEdge(*nodes[1], *nodes[2]);
  graph.addEdge(*nodes[2], *nodes[1]);
  graph.addEdge(*nodes[3], *nodes[1]);
  graph.addEdge(*nodes[3], *nodes[2]);

  ASSERT_TRUE(reachablePartIsReducible(graph, nodes[0]));
  ASSERT_TRUE(unreachableDecisionReachesCycle(graph, nodes[0]));

  auto inevitability = lotus::cd::detail::computeInevitability(graph);
  EXPECT_EQ(bruteDOD(graph, inevitability), (std::set<Triple>{{4, 2, 3}}));
  EXPECT_FALSE(
      lotus::cd::detail::computeCompactDOD(graph, inevitability).empty());
  size_t baselinePairs = 0;
  lotus::cd::detail::forEachBaselineDODPair(
      graph, [&](auto *, auto *, auto *) { ++baselinePairs; });
  EXPECT_EQ(baselinePairs, 1u);
}

// The library guard must agree with the reference predicate above, which the
// preceding tests tie to an empty DOD. Compare them on every four-vertex graph
// and on random larger graphs, where the dominator computation has more room to
// go wrong, and check the implication on those larger graphs as well.
TEST(ControlDependenceTest, LibraryReducibilityGuardMatchesReference) {
  auto reference = [](lotus::cd::detail::Graph &graph,
                      lotus::cd::detail::GraphNode *start) {
    return reachablePartIsReducible(graph, start) &&
           !unreachableDecisionReachesCycle(graph, start);
  };

  constexpr unsigned nodeCount = 4;
  constexpr unsigned graphCount = 1u << (nodeCount * nodeCount);
  for (unsigned mask = 0; mask < graphCount; ++mask) {
    lotus::cd::detail::Graph graph;
    std::vector<lotus::cd::detail::GraphNode *> nodes;
    for (unsigned index = 0; index < nodeCount; ++index)
      nodes.push_back(&graph.createNode());
    for (unsigned source = 0; source < nodeCount; ++source)
      for (unsigned target = 0; target < nodeCount; ++target)
        if (mask & (1u << (source * nodeCount + target)))
          graph.addEdge(*nodes[source], *nodes[target]);
    ASSERT_EQ(lotus::cd::detail::isDODEmptyByReducibility(graph, *nodes[0]),
              reference(graph, nodes[0]))
        << "graph mask " << mask;
  }

  constexpr unsigned trials = 20000;
  const double densities[] = {0.15, 0.25, 0.35};
  std::mt19937 rng(20260921);
  unsigned guarded = 0;
  unsigned unguardedWithDOD = 0;
  for (unsigned trial = 0; trial < trials; ++trial) {
    unsigned size = 5 + trial % 5;
    std::bernoulli_distribution edge(densities[trial % 3]);
    lotus::cd::detail::Graph graph;
    std::vector<lotus::cd::detail::GraphNode *> nodes;
    for (unsigned index = 0; index < size; ++index)
      nodes.push_back(&graph.createNode());
    for (unsigned source = 0; source < size; ++source)
      for (unsigned target = 0; target < size; ++target)
        if (edge(rng))
          graph.addEdge(*nodes[source], *nodes[target]);

    bool guard = lotus::cd::detail::isDODEmptyByReducibility(graph, *nodes[0]);
    ASSERT_EQ(guard, reference(graph, nodes[0])) << "random trial " << trial;
    auto inevitability = lotus::cd::detail::computeInevitability(graph);
    bool empty = bruteDOD(graph, inevitability).empty();
    if (guard) {
      ++guarded;
      ASSERT_TRUE(empty) << "random trial " << trial;
    } else {
      unguardedWithDOD += !empty;
    }
  }

  std::cout << "[ SWEEP    ] " << graphCount << " four-vertex graphs and "
            << trials << " random graphs of 5-9 vertices, " << guarded
            << " random graphs guarded, " << unguardedWithDOD
            << " unguarded random graphs with a non-empty DOD\n";
  EXPECT_GT(guarded, 0u);
  EXPECT_GT(unguardedWithDOD, 0u);
}

} // namespace
