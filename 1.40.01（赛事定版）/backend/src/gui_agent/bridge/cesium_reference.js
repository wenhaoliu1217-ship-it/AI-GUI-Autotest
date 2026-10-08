export function installCesiumTestBridge(viewer, options = {}) {
  if (!viewer?.scene || !viewer?.camera || !viewer?.entities) {
    throw new Error("A Cesium Viewer instance is required");
  }
  const CesiumApi = options.Cesium || globalThis.Cesium;
  if (!CesiumApi?.SceneTransforms) {
    throw new Error("Cesium SceneTransforms is required");
  }
  const globalName = options.globalName || "__WEB_AI_TEST__";
  const readyTimeoutMs = options.readyTimeoutMs || 15000;
  let selectedTargetId = null;

  const targetId = (entity) => String(entity?.id || "");
  const entityPosition = (entity) => {
    const time = viewer.clock?.currentTime;
    return entity?.position?.getValue ? entity.position.getValue(time) : null;
  };
  const screenPosition = (entity) => {
    const world = entityPosition(entity);
    if (!world) return null;
    const point = CesiumApi.SceneTransforms.worldToWindowCoordinates(viewer.scene, world);
    return point && Number.isFinite(point.x) && Number.isFinite(point.y)
      ? { x: Number(point.x), y: Number(point.y) }
      : null;
  };
  const findEntity = (id) => viewer.entities.values.find((entity) => targetId(entity) === id);

  const bridge = {
    version: "1",
    getSceneState() {
      const camera = viewer.camera;
      const layers = viewer.imageryLayers;
      return {
        camera: {
          position: camera.positionWC ? { x: camera.positionWC.x, y: camera.positionWC.y, z: camera.positionWC.z } : null,
          heading: Number(camera.heading),
          pitch: Number(camera.pitch),
          roll: Number(camera.roll),
        },
        clock: viewer.clock?.currentTime?.toString?.() || null,
        layers: Array.from({ length: layers?.length || 0 }, (_, index) => ({
          index,
          show: Boolean(layers.get(index)?.show),
        })),
        tilesLoaded: Boolean(viewer.scene.globe?.tilesLoaded),
        loading: !viewer.scene.globe?.tilesLoaded,
      };
    },
    listVisibleTargets() {
      return viewer.entities.values.flatMap((entity) => {
        if (entity.show === false) return [];
        const position = screenPosition(entity);
        if (!position) return [];
        return [{
          id: targetId(entity),
          type: "entity",
          label: String(entity.name || entity.id),
          position,
        }];
      });
    },
    getTargetScreenPosition(id) {
      const entity = findEntity(id);
      const position = entity && screenPosition(entity);
      if (!position) throw new Error(`Cesium target is not visible: ${id}`);
      return position;
    },
    getSelectedTargetId() {
      return selectedTargetId;
    },
    async waitForSceneReady() {
      const started = Date.now();
      let stableFrames = 0;
      while (Date.now() - started < readyTimeoutMs) {
        const ready = Boolean(viewer.scene.globe?.tilesLoaded) && viewer.scene.mode !== CesiumApi.SceneMode?.MORPHING;
        stableFrames = ready ? stableFrames + 1 : 0;
        if (stableFrames >= 2) return { ready: true };
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
      throw new Error("Cesium scene did not become ready before timeout");
    },
  };

  const clickHandler = (event) => {
    const rect = viewer.scene.canvas.getBoundingClientRect();
    const position = new CesiumApi.Cartesian2(event.clientX - rect.left, event.clientY - rect.top);
    const picked = viewer.scene.pick(position);
    selectedTargetId = picked?.id ? targetId(picked.id) : null;
  };
  viewer.scene.canvas.addEventListener("click", clickHandler);
  Object.defineProperty(bridge, "dispose", {
    value: () => viewer.scene.canvas.removeEventListener("click", clickHandler),
    enumerable: false,
  });
  globalThis[globalName] = bridge;
  return bridge;
}
