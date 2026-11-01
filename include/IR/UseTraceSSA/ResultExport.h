#pragma once

#include "IR/UseTraceSSA/DefectDetector.h"

#include <functional>
#include <map>
#include <ostream>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace lotus {
namespace usetracessa {
namespace cli {

/// One witness step: the flow-node label and its source location (may be empty).
using WitnessStep = std::pair<std::string, std::string>;

inline void writeJSONString(std::ostream &os, const std::string &text) {
  static const char *hex = "0123456789abcdef";
  os << '"';
  for (unsigned char c : text) {
    if (c == '"' || c == '\\') os << '\\' << c;
    else if (c == '\n') os << "\\n";
    else if (c == '\t') os << "\\t";
    else if (c < 0x20) os << "\\u00" << hex[c >> 4] << hex[c & 15];
    else os << c;
  }
  os << '"';
}

inline const char *statusText(QueryStatus status) {
  switch (status) {
  case QueryStatus::Found: return "Found";
  case QueryStatus::NotFound: return "NotFound";
  case QueryStatus::Unknown: return "Unknown";
  }
  return "Unknown";
}

/// Machine-readable check results for the differential oracle and manual
/// sampling: per-object statuses, and findings deduplicated by sink site with
/// the union of their objects. siteOf names a finding's sink site; witnessOf,
/// when set, returns the witness of a finding (called once per site);
/// objectKey, when set, adds a run-independent name to every object, and
/// objectLocation the source location of its allocation.
inline void writeResultsJSON(
    std::ostream &os, const std::string &tool, const std::string &check,
    const std::map<std::string, std::string> &configuration,
    const DefectScan &scan,
    const std::function<std::string(std::size_t)> &siteOf,
    const std::function<std::vector<WitnessStep>(std::size_t)> &witnessOf = {},
    const std::function<std::string(ObjectID)> &objectKey = {},
    const std::function<std::string(ObjectID)> &objectLocation = {}) {
  std::map<std::string, std::pair<std::size_t, std::set<ObjectID>>> sites;
  for (std::size_t index = 0; index < scan.findings.size(); ++index) {
    auto &site = sites.emplace(siteOf(index), std::make_pair(index, std::set<ObjectID>{}))
                     .first->second;
    const auto &objects = scan.findings[index].objects;
    site.second.insert(objects.begin(), objects.end());
    if (objects.empty() && scan.findings[index].witnessObject)
      site.second.insert(*scan.findings[index].witnessObject);
  }
  os << "{\"schema\":1,\"tool\":";
  writeJSONString(os, tool);
  os << ",\"check\":";
  writeJSONString(os, check);
  for (const auto &option : configuration) {
    os << ',';
    writeJSONString(os, option.first);
    os << ':';
    writeJSONString(os, option.second);
  }
  os << ",\"status\":\"" << statusText(scan.status) << "\",\"exhaustive\":"
     << (scan.exhaustive ? "true" : "false") << ",\"objects\":[";
  bool first = true;
  for (const auto &object : scan.objectStatus) {
    os << (first ? "" : ",") << "{\"object\":" << object.first;
    if (objectKey) {
      os << ",\"key\":";
      writeJSONString(os, objectKey(object.first));
    }
    if (objectLocation) {
      os << ",\"location\":";
      writeJSONString(os, objectLocation(object.first));
    }
    os << ",\"status\":\"" << statusText(object.second) << "\"}";
    first = false;
  }
  os << "],\"findings\":[";
  first = true;
  for (const auto &site : sites) {
    os << (first ? "" : ",") << "{\"site\":";
    writeJSONString(os, site.first);
    os << ",\"objects\":[";
    bool firstObject = true;
    for (auto object : site.second.second) {
      os << (firstObject ? "" : ",") << object;
      firstObject = false;
    }
    os << ']';
    if (objectKey) {
      os << ",\"object_keys\":[";
      firstObject = true;
      for (auto object : site.second.second) {
        os << (firstObject ? "" : ",");
        writeJSONString(os, objectKey(object));
        firstObject = false;
      }
      os << ']';
    }
    if (witnessOf) {
      os << ",\"witness\":[";
      bool firstStep = true;
      for (const auto &step : witnessOf(site.second.first)) {
        os << (firstStep ? "" : ",") << "{\"label\":";
        writeJSONString(os, step.first);
        os << ",\"location\":";
        writeJSONString(os, step.second);
        os << '}';
        firstStep = false;
      }
      os << ']';
    }
    os << '}';
    first = false;
  }
  os << "]}\n";
}

} // namespace cli
} // namespace usetracessa
} // namespace lotus
