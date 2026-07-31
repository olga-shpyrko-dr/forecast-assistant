import { useCallback, useEffect, useState } from "react";
import getRegistryDatasets, {
  type RegistryDataset,
} from "~/api/getRegistryDatasets";

const useRegistryDatasets = () => {
  const [datasets, setDatasets] = useState<RegistryDataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDatasets = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getRegistryDatasets();
      setDatasets(data);
    } catch (err) {
      console.error("Error fetching registry datasets", err);
      setError("Failed to load datasets from the Data Registry");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDatasets();
  }, [fetchDatasets]);

  return { datasets, loading, error, refetch: fetchDatasets };
};

export default useRegistryDatasets;
