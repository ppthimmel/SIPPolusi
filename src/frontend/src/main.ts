import maplibregl from "maplibre-gl";

// Presentation layer (PF-01, PF-02, PF-09, PF-10, PF-11, PF-12, PNF-07, PNF-08):
// origin/destination/mode/preference input, recommended + baseline route,
// pollutant exposure heatmap, confidence score, and fallback disclaimers.

const map = new maplibregl.Map({
  container: "map",
  style: "https://demotiles.maplibre.org/style.json",
  center: [106.8456, -6.2088], // Jakarta
  zoom: 11,
});

map.addControl(new maplibregl.NavigationControl());

async function planRoute(request: {
  origin: { lat: number; lon: number };
  destination: { lat: number; lon: number };
  mode: "walk" | "bike";
  alpha: number;
  beta: number;
}) {
  const response = await fetch("/v1/routes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  return response.json();
}

export { planRoute };
