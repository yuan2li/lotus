add_library(CanaryUseTraceSSACore STATIC
  UseTraceSSA.cpp
  TraceFlowGraph.cpp
  Query.cpp
  ObjectQuery.cpp
  TemporalHistory.cpp
  SVFGImporter.cpp
  Models.cpp
  DefectDetector.cpp
)
set_target_properties(CanaryUseTraceSSACore PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_link_libraries(CanaryUseTraceSSACore PUBLIC LLVMSupport)

# The API reports invalid construction through C++ exceptions. Do not inherit
# LLVM's optional -fno-exceptions setting in this component or its consumers.
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(CanaryUseTraceSSACore PUBLIC -fexceptions)
endif()

add_library(CanaryUseTraceSSA STATIC LLVMImporter.cpp)
target_link_libraries(CanaryUseTraceSSA PUBLIC CanaryUseTraceSSACore LLVMCore LLVMAnalysis LLVMSupport)
set_target_properties(CanaryUseTraceSSA PROPERTIES POSITION_INDEPENDENT_CODE ON)

add_library(CanaryUseTraceSSASVFG STATIC SVFGBridge.cpp)
target_link_libraries(CanaryUseTraceSSASVFG PUBLIC CanaryUseTraceSSA SVFG)

add_library(CanaryUseTraceSSAPath STATIC PathFeasibility.cpp)
target_link_libraries(CanaryUseTraceSSAPath PUBLIC CanaryUseTraceSSASVFG ${Z3_LIBRARIES})
