// Standalone data and output adapter for unmodified upstream methods.
// This adapter does not implement a transport algorithm.
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <map>
#include <ranges>
#include <set>
#include <tuple>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace qc {
using Qubit = std::size_t;
}

namespace na::zoned {
struct Atom {
  std::size_t id;
};
struct Location {
  double x;
  double y;
};
struct SLM {
  std::pair<std::size_t, std::size_t> siteSeparation;
  std::size_t nRows;
  std::size_t nCols;
  std::pair<std::size_t, std::size_t> location;
  const std::array<SLM, 2>* entanglementZone_ = nullptr;
  bool isEntanglement() const { return entanglementZone_ != nullptr; }
  bool isStorage() const { return !isEntanglement(); }
  bool operator==(const SLM& other) const {
    return location == other.location && nRows == other.nRows &&
           nCols == other.nCols && siteSeparation == other.siteSeparation &&
           entanglementZone_ == other.entanglementZone_;
  }
};
using Site =
    std::tuple<std::reference_wrapper<const SLM>, std::size_t, std::size_t>;
using Placement = std::vector<Site>;
struct Architecture {
  auto exactSLMLocation(const SLM& slm, std::size_t r,
                        std::size_t c) const
      -> std::pair<std::size_t, std::size_t> {
    assert(r < slm.nRows && c < slm.nCols);
    return {slm.siteSeparation.first * c + slm.location.first,
            slm.siteSeparation.second * r + slm.location.second};
  }
};
struct LoadOp {};
struct MoveOp {};
struct StoreOp {};

class NAComputation {
  bool first_ = true;

  void prefix(const char* kind, const std::vector<const Atom*>& atoms) {
    if (!first_) {
      std::cout << ',';
    }
    first_ = false;
    std::cout << "{\"kind\":\"" << kind << "\",\"atoms\":[";
    for (std::size_t i = 0; i < atoms.size(); ++i) {
      if (i) {
        std::cout << ',';
      }
      std::cout << atoms[i]->id;
    }
    std::cout << ']';
  }

public:
  template <class Op>
  void emplaceBack(const std::vector<const Atom*>& atoms) {
    static_assert(std::is_same_v<Op, LoadOp> || std::is_same_v<Op, StoreOp>);
    prefix(std::is_same_v<Op, LoadOp> ? "load" : "store", atoms);
    std::cout << '}';
  }

  template <class Op>
  void emplaceBack(const std::vector<const Atom*>& atoms,
                   const std::vector<Location>& positions) {
    static_assert(std::is_same_v<Op, MoveOp>);
    assert(atoms.size() == positions.size());
    prefix("move", atoms);
    std::cout << ",\"targets_nm\":[";
    for (std::size_t i = 0; i < positions.size(); ++i) {
      if (i) {
        std::cout << ',';
      }
      std::cout << '[' << positions[i].x << ',' << positions[i].y << ']';
    }
    std::cout << "]}";
  }
};
