const express = require("express");
const path = require("path");
const { GoogleGenAI } = require("@google/genai");

const app = express();
const PORT = 3000;

const ai = new GoogleGenAI({
    apiKey: process.env.GEMINI_API_KEY
});

app.use(express.json());

app.use(express.static(path.join(__dirname, "public")));

app.get("/", (req, res) => {
    res.sendFile(
        path.join(__dirname, "public", "index.html")
    );
});

app.post("/api/chat", async (req, res) => {
    try {
        const messages = req.body.messages || [];

        if (!messages.length) {
            return res.status(400).json({
                error: "Không có tin nhắn."
            });
        }

        const conversation = messages
            .map(message => {
                const role =
                    message.role === "assistant"
                        ? "Assistant"
                        : "User";

                return `${role}: ${message.content}`;
            })
            .join("\n\n");

        const prompt = `
Bạn là SmartAI, một trợ lý AI thông minh, thân thiện và hữu ích.

Quy tắc:
- Nếu người dùng nói tiếng Việt, trả lời bằng tiếng Việt.
- Nếu người dùng nói tiếng Anh, có thể trả lời bằng tiếng Anh.
- Trả lời rõ ràng, dễ hiểu.
- Không bịa thông tin.
- Không nói rằng bạn là Gemini trừ khi người dùng hỏi trực tiếp.
- Hãy trả lời tự nhiên như một trợ lý AI.

Lịch sử cuộc trò chuyện:

${conversation}

Hãy trả lời tin nhắn cuối cùng của người dùng.
`;

        const response = await ai.models.generateContent({
            model: "gemini-3.7-flash",
            contents: prompt
        });

        res.json({
            answer: response.text
        });

    } catch (error) {
        console.error("GEMINI ERROR:", error);

        res.status(500).json({
            error:
                error.message ||
                "Lỗi kết nối Gemini."
        });
    }
});

app.listen(PORT, () => {
    console.log(
        `SMART AI đang chạy tại http://localhost:${PORT}`
    );
});