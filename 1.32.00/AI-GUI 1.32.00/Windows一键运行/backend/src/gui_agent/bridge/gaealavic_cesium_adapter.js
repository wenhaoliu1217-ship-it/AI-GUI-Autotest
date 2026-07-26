export function installGAEALaViCCesiumTestBridge(options) {
  const { viewer, hooks, Cesium: CesiumApi = globalThis.Cesium } = options || {};
  if (!viewer?.scene || !viewer?.camera || !viewer?.entities || !CesiumApi?.SceneTransforms) {
    throw new Error("GAEALaViC adapter requires a Cesium Viewer and Cesium API");
  }
  const requiredHooks = ["getLayerMetadata", "getBusinessSceneState", "listVisibleTargets", "getTargetScreenPosition", "getSelectedTargetId"];
  const missing = requiredHooks.filter((name) => typeof hooks?.[name] !== "function");
  if (missing.length) throw new Error(`GAEALaViC adapter hooks missing: ${missing.join(", ")}`);
  const globalName = options.globalName || "__WEB_AI_TEST__";
  const readyTimeoutMs = options.readyTimeoutMs || 15000;
  let webglError = null;
  const onContextLost = () => { webglError = "webglcontextlost"; };
  viewer.scene.canvas.addEventListener("webglcontextlost", onContextLost);

  const normalizedState = () => {
    const business = hooks.getBusinessSceneState();
    for (const key of ["pathPoints", "pois", "fences", "drawings"]) {
      if (!Array.isArray(business?.[key])) throw new Error(`GAEALaViC semantic state ${key} must be an array`);
    }
    const camera = viewer.camera;
    const layers = Array.from({ length: viewer.imageryLayers?.length || 0 }, (_, index) => {
      const layer = viewer.imageryLayers.get(index);
      const metadata = hooks.getLayerMetadata(layer, index);
      if (!metadata?.id || !metadata?.name) throw new Error(`Layer ${index} is missing id/name metadata`);
      return { id: String(metadata.id), name: String(metadata.name), visible: Boolean(layer.show) };
    });
    return {
      camera: {
        longitude: Number(hooks.cameraLongitude?.(camera)),
        latitude: Number(hooks.cameraLatitude?.(camera)),
        height: Number(hooks.cameraHeight?.(camera)),
        heading: Number(camera.heading), pitch: Number(camera.pitch), roll: Number(camera.roll),
      },
      layers,
      entityCount: Number(viewer.entities.values.length),
      selectedEntityId: hooks.getSelectedTargetId(),
      pathPoints: business.pathPoints,
      pois: business.pois,
      fences: business.fences,
      drawings: business.drawings,
      tilesLoaded: Boolean(viewer.scene.globe?.tilesLoaded),
      loading: !viewer.scene.globe?.tilesLoaded,
      webglError: webglError || business.webglError || null,
    };
  };
  const bridge = {
    version: "1",
    getSceneState: normalizedState,
    listVisibleTargets: () => hooks.listVisibleTargets(),
    getTargetScreenPosition: (id) => hooks.getTargetScreenPosition(id),
    getSelectedTargetId: () => hooks.getSelectedTargetId(),
    async waitForSceneReady() {
      const started = Date.now(); let stableFrames = 0;
      while (Date.now() - started < readyTimeoutMs) {
        const ready = Boolean(viewer.scene.globe?.tilesLoaded) && !webglError;
        stableFrames = ready ? stableFrames + 1 : 0;
        if (stableFrames >= 2) return { ready: true };
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
      throw new Error("GAEALaViC Cesium scene did not become ready before timeout");
    },
  };
  Object.defineProperty(bridge, "dispose", {
    enumerable: false,
    value: () => viewer.scene.canvas.removeEventListener("webglcontextlost", onContextLost),
  });
  globalThis[globalName] = bridge;
  return bridge;
}
