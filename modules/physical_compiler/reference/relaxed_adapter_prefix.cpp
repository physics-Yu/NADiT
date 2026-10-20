// Coordinate and JSON adapters for unmodified upstream routing methods.
// No compatibility, graph, grouping, or merge decisions are implemented here.
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <list>
#include <optional>
#include <ranges>
#include <set>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

// Configuration serialization is unrelated to the executed router.
#define NLOHMANN_DEFINE_TYPE_INTRUSIVE_WITH_DEFAULT(...)

namespace qc {
using Qubit = std::size_t;
}

namespace na::zoned {
struct SLM {
  std::pair<std::size_t, std::size_t> siteSeparation{1, 1};
  std::size_t nRows;
  std::size_t nCols;
  std::pair<std::size_t, std::size_t> location{0, 0};
};
using Site =
    std::tuple<std::reference_wrapper<const SLM>, std::size_t, std::size_t>;
using Placement = std::vector<Site>;
using Routing = std::vector<std::vector<qc::Qubit>>;

struct Architecture {
  auto exactSLMLocation(const SLM& slm, std::size_t r, std::size_t c) const
      -> std::pair<std::size_t, std::size_t> {
    assert(r < slm.nRows && c < slm.nCols);
    return {slm.siteSeparation.first * c + slm.location.first,
            slm.siteSeparation.second * r + slm.location.second};
  }
  auto distance(const SLM& first, std::size_t r1, std::size_t c1,
                const SLM& second, std::size_t r2, std::size_t c2) const
      -> double {
    const auto [x1, y1] = exactSLMLocation(first, r1, c1);
    const auto [x2, y2] = exactSLMLocation(second, r2, c2);
    return std::hypot(static_cast<double>(x1) - static_cast<double>(x2),
                      static_cast<double>(y1) - static_cast<double>(y2));
  }
};

struct RouterBase {
  virtual ~RouterBase() = default;
  virtual auto route(const std::vector<Placement>& placement) const
      -> std::vector<Routing> = 0;
};
} // namespace na::zoned
