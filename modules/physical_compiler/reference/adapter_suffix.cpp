} // namespace na::zoned

int main() {
  using namespace na::zoned;
  std::cout << std::setprecision(17);
  auto readSLM = []() {
    SLM slm{};
    std::cin >> slm.location.first >> slm.location.second >>
        slm.siteSeparation.first >> slm.siteSeparation.second >> slm.nRows >>
        slm.nCols;
    return slm;
  };
  const SLM storage = readSLM();
  std::array<SLM, 2> gate{readSLM(), readSLM()};
  gate[0].entanglementZone_ = &gate;
  gate[1].entanglementZone_ = &gate;
  const std::array<const SLM*, 3> slms{&storage, &gate[0], &gate[1]};
  std::size_t count;
  std::cin >> count;
  std::vector<Atom> ownedAtoms;
  ownedAtoms.reserve(count);
  Placement source, target;
  for (std::size_t id = 0; id < count; ++id) {
    std::size_t s, sr, sc, t, tr, tc;
    std::cin >> s >> sr >> sc >> t >> tr >> tc;
    ownedAtoms.push_back({id});
    source.emplace_back(*slms.at(s), sr, sc);
    target.emplace_back(*slms.at(t), tr, tc);
  }
  std::vector<std::reference_wrapper<const Atom>> atoms;
  for (const auto& atom : ownedAtoms) {
    atoms.emplace_back(atom);
  }
  std::size_t moveCount;
  std::cin >> moveCount;
  std::vector<qc::Qubit> movers(moveCount);
  for (auto& id : movers) {
    std::cin >> id;
  }
  if (!std::cin) {
    return 2;
  }
  const Architecture architecture;
  CodeGenerator::RearrangementGenerator generator(architecture, source, target,
                                                  movers);
  NAComputation code;
  std::cout << '[';
  generator.generate(atoms, code);
  std::cout << "]\n";
}
