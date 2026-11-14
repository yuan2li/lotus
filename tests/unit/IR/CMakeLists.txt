add_subdirectory(PDG)
add_subdirectory(GVFG)
add_subdirectory(ShadowMemSSA)
add_subdirectory(GSA)
add_subdirectory(SVFG)

# All IR test sources collected from the subdirectories above are linked into
# one binary so the shared libraries are linked only once.
lotus_collect_test_sources(ir_tests
  GraphViewTest.cpp
  UseTraceSSA/FactorizedTest.cpp
  UseTraceSSA/ScalingTest.cpp
  UseTraceSSA/NativeFactorizedTest.cpp
  UseTraceSSA/PathFeasibilityTest.cpp
  UseTraceSSA/HistoryTest.cpp
  UseTraceSSA/LLVMHistoryTest.cpp
  UseTraceSSA/FlowTest.cpp
  UFG/UFGTest.cpp)

add_lotus_collected_test_suite(ir_tests
  LINK_LIBS
    CanaryICFG
    CanaryPDG
    CanaryAliasAnalysisWrapper
    CanaryGSA
    CanaryGuardedValueFlow
    CanaryShadowMemSSA
    CanaryOptimizationIPO
    SVFG
    CanaryUseTraceSSACore
    CanaryUseTraceSSASVFG
    CanaryUseTraceSSAPath
    CanaryUFG
    ${Z3_LIBRARIES})

# Resolve fixtures independently of CTest's working directory. The CLI smoke
# test uses the selected build's executable, including multi-config builds.
target_compile_definitions(ir_tests PRIVATE
  LOTUS_TEST_SOURCE_DIR="${PROJECT_SOURCE_DIR}")
if(TARGET lotus-ir-pdg-query)
  target_compile_definitions(ir_tests PRIVATE
    LOTUS_PDG_QUERY_TOOL_PATH="$<TARGET_FILE:lotus-ir-pdg-query>")
  add_dependencies(ir_tests lotus-ir-pdg-query)
endif()
