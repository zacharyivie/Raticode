// A provider can hide an alias whose full model is already listed. The alias still
// appears where it is the saved choice, so saved work never shows as unavailable.
export function menuModels(provider, ...selected) {
  return (provider?.models ?? []).filter(model => model.listed !== false || selected.includes(model.id));
}
