// Data-only adapters for the complete pinned algorithm bodies.
#include <chrono>
#include <list>
#include <sstream>
#include <stack>
#define SPDLOG_WARN(...)
#define SPDLOG_INFO(...)
namespace qc {
constexpr double PI = 3.14159265358979323846;
constexpr double PI_2 = PI / 2;
constexpr double PI_4 = PI / 4;
enum OpType { Z, RZ, RY, RX, U, U2, P, S, Sdg, T, Tdg, H, X, Y,
              V, Vdg, SX, SXdg, Barrier };
inline auto toString(OpType type) -> std::string {
  const char* names[] = {"Z","RZ","RY","RX","U","U2","P","S","Sdg","T",
                        "Tdg","H","X","Y","V","Vdg","SX","SXdg","Barrier"};
  return names[type];
}
struct Control { Qubit qubit; };
struct Operation {
  size_t inputIndex{};
  OpType type{};
  std::vector<Qubit> targets;
  std::vector<Control> controls;
  std::vector<double> parameters;
  virtual ~Operation() = default;
  auto getType() const { return type; }
  auto getNqubits() const { return targets.size() + controls.size(); }
  auto getNtargets() const { return targets.size(); }
  auto getNcontrols() const { return controls.size(); }
  auto isControlled() const { return !controls.empty(); }
  auto isGlobal(size_t n) const { return getNqubits() == n; }
  virtual auto isCompoundOperation() const -> bool { return false; }
  virtual auto isStandardOperation() const -> bool { return true; }
  const auto& getTargets() const { return targets; }
  const auto& getControls() const { return controls; }
  const auto& getParameter() const { return parameters; }
};
struct StandardOperation : Operation {};
struct CompoundOperation : Operation {
  std::vector<std::unique_ptr<Operation>> operations;
  auto isCompoundOperation() const -> bool override { return true; }
  auto isStandardOperation() const -> bool override { return false; }
  const auto& front() const { return operations.front(); }
};
struct QuantumComputation {
  size_t nQubits;
  std::vector<std::unique_ptr<Operation>> operations;
  auto getNqubits() const { return nQubits; }
  auto empty() const { return operations.empty(); }
  auto begin() const { return operations.begin(); }
  auto end() const { return operations.end(); }
};
}
namespace na::zoned {
struct Atom { size_t id; };
struct Location { double x, y; };
struct Zone {
  struct Extent { double minX, minY, maxX, maxY; };
  size_t id;
  std::string name;
  Extent extent;
};
struct LoadOp {};
struct MoveOp {};
struct StoreOp {};
struct GlobalRYOp {};
struct GlobalCZOp {};
struct LocalRZOp {};
struct LocalUOp {};
struct Event {
  std::string kind;
  std::vector<size_t> atoms;
  std::vector<Location> targets;
  std::vector<double> parameters;
  std::vector<size_t> zones;
};
struct NAComputation {
  std::deque<Atom> atoms;
  std::deque<Zone> zones;
  std::vector<Location> initial;
  std::vector<Event> events;
  const auto& emplaceBackZone(std::string name, Zone::Extent extent) {
    return zones.emplace_back(zones.size(), std::move(name), extent);
  }
  const auto& emplaceBackAtom(const std::string&) {
    return atoms.emplace_back(atoms.size());
  }
  void emplaceInitialLocation(const Atom&, double x, double y) {
    initial.push_back({x,y});
  }
  template <class Op>
  void emplaceBack(const std::vector<const Atom*>& affected) {
    Event event;
    event.kind = std::is_same_v<Op, LoadOp> ? "load" : "store";
    static_assert(std::is_same_v<Op, LoadOp> || std::is_same_v<Op, StoreOp>);
    for (const auto* atom : affected) event.atoms.push_back(atom->id);
    events.push_back(std::move(event));
  }
  template <class Op>
  void emplaceBack(const std::vector<const Atom*>& affected,
                   const std::vector<Location>& locations) {
    static_assert(std::is_same_v<Op, MoveOp>);
    Event event{"move"};
    for (const auto* atom : affected) event.atoms.push_back(atom->id);
    event.targets = locations;
    events.push_back(std::move(event));
  }
  template <class Op>
  void emplaceBack(const std::vector<const Zone*>& affected) {
    static_assert(std::is_same_v<Op, GlobalCZOp>);
    Event event{"global_cz"};
    for (const auto* zone : affected) event.zones.push_back(zone->id);
    events.push_back(std::move(event));
  }
  template <class Op>
  void emplaceBack(const Zone& zone, double angle) {
    static_assert(std::is_same_v<Op, GlobalRYOp>);
    events.push_back({"global_ry", {}, {}, {angle}, {zone.id}});
  }
  template <class Op>
  void emplaceBack(const Atom& atom, double angle) {
    static_assert(std::is_same_v<Op, LocalRZOp>);
    events.push_back({"local_rz", {atom.id}, {}, {angle}, {}});
  }
  template <class Op>
  void emplaceBack(const Atom& atom, double theta, double phi, double lambda) {
    static_assert(std::is_same_v<Op, LocalUOp>);
    events.push_back({"local_u", {atom.id}, {}, {theta,phi,lambda}, {}});
  }
};
}
