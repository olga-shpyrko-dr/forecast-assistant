import { useCallback, useEffect, useState } from "react";
import getScoringDataVersions, {
  type ScoringDataVersion,
} from "~/api/getScoringDataVersions";

const useDatasetVersions = (activeDatasetId?: string) => {
  const [versions, setVersions] = useState<ScoringDataVersion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchVersions = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await getScoringDataVersions(activeDatasetId);
      setVersions(data);
    } catch (err) {
      console.error("Error fetching dataset versions", err);
      setError("Failed to load dataset versions");
    } finally {
      setLoading(false);
    }
  }, [activeDatasetId]);

  useEffect(() => {
    fetchVersions();
  }, [fetchVersions]);

  return { versions, loading, error, refetch: fetchVersions };
};

export default useDatasetVersions;
